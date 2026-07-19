"""Stage A — pull catalogue metadata + triage flags out of the already-OCR'd corpus.

The 6.8 GPU-days of OCR are already banked in LanceDB (353,604 chunks with their text),
so nothing is re-OCR'd here: we read each document's opening chunks, ask the local model
for title/authors/year/type, and decide whether the record is safe to publish.

Writes ONE JSON object per line to catalog_out/metadata.jsonl. Resumable — rerun after a
crash/reboot and it skips whatever is already in the file. Nothing is written to eLibrary;
Stage B (php artisan catalog:import-corpus) does that.

Triage — anything flagged is imported ON HOLD (unpublished) for a human to sort out:
  restricted  the document says, or its filename says, it isn't for open circulation
  no-title    the model couldn't find a title -> uncatalogable as-is
  junk        not research output (forms, receipts, memos, blank scans)
Everything else is published.

Usage:
    python catalog_extract.py                # run (resumes)
    python catalog_extract.py --limit 25     # small sample
    python catalog_extract.py --stats        # summarise what's been extracted so far
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import lancedb
import requests

HERE = Path(__file__).resolve().parent
PDF_DIR = HERE / "pdf"
OUT_DIR = HERE / "catalog_out"
OUT_FILE = OUT_DIR / "metadata.jsonl"
LLAMA = os.environ.get("LLAMA_URL", "http://127.0.0.1:8080/v1/chat/completions")

# Filename/text hints that a document isn't meant for open circulation. Deliberately
# broad: a false positive only costs a librarian one click in the on-hold list, a false
# negative publishes something that shouldn't be public.
RESTRICTED_PAT = re.compile(
    r"\b(restricted|confidential|not\s+for\s+(public|circulation|distribution)|"
    r"internal\s+use\s+only|for\s+official\s+use\s+only|embargo(ed)?|do\s+not\s+distribute)\b",
    re.I,
)

PROMPT = """You are cataloguing a document for a research library. Below is the opening text of a scanned document.

Reply with ONLY a JSON object, no prose, no markdown fence:
{"title": string|null,
 "authors": string|null,
 "statement_of_responsibility": string|null,
 "year": string|null,
 "publisher": string|null,
 "language": string|null,
 "subjects": string|null,
 "type": "Books"|"Serials"|"Thesis/Dissertations"|"Technical Reports"|"Investigatory Projects"|"Vertical Files"|"Non-Prints",
 "is_research": true|false,
 "restricted": true|false}

Rules:
- title: the document's actual title (MARC 245$a). null if there is no discernible title.
- authors: personal or corporate authors, separated by " /" (MARC 100$a).
  e.g. "Schreuder, Hans T. /Sedransk, Joseph". Surname-first where you can tell. null if not stated.
- statement_of_responsibility: the "by ..." line as printed (MARC 245$c), e.g. "by Hans T. Schreuder...[et al.]".
- year: 4-digit year of publication (MARC 260$c). null if not stated.
- publisher: publishing body / institution / university (MARC 260$b). null if not stated.
- language: language of the text, e.g. "English", "Filipino". (MARC 041$a)
- subjects: topical keywords/subject terms, space-separated as printed (MARC 650$a).
  Use the document's own "Keywords:" line if present, otherwise the main topics. null if unclear.
- type: best fit. Journal/newsletter/periodical -> Serials. Thesis/dissertation -> Thesis/Dissertations.
  Annual/terminal/project report -> Technical Reports. Book/monograph/proceedings -> Books.
- is_research: false if this is NOT research output -- e.g. an application/enrolment form,
  receipt, invoice, memo, blank page, letterhead, ID, or administrative paperwork.
- restricted: true if the text says it is restricted, confidential, embargoed, for internal
  or official use only, or otherwise not for public distribution.
- Never guess or invent. Use null when the document does not state something.

TEXT:
---
%s
---"""


# Stop after this many backend failures in a row (see the Open WebUI incident in
# check_backend): one dead endpoint should cost a few records, not the whole corpus.
MAX_CONSECUTIVE_ERRORS = 8


def load_done() -> set[str]:
    """Stems already extracted (resume support).

    Records flagged 'error' are NOT counted as done — the failure was the backend's, not
    the document's, so a rerun must retry them.
    """
    done = set()
    if OUT_FILE.exists():
        with OUT_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:  # noqa: BLE001  - a torn last line after a hard kill
                    continue
                if "error" in (r.get("flags") or []):
                    continue
                done.add(r["stem"])
    return done


def purge_errors_and_report() -> int:
    """Drop backend-error rows from the JSONL so a rerun retries those documents."""
    if not OUT_FILE.exists():
        return 1

    kept, dropped = [], 0
    with OUT_FILE.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:  # noqa: BLE001
                dropped += 1
                continue
            if "error" in (r.get("flags") or []):
                dropped += 1
                continue
            kept.append(line)

    if dropped:
        tmp = OUT_FILE.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        tmp.replace(OUT_FILE)
        print(f"purged {dropped:,} failed record(s); {len(kept):,} good record(s) kept "
              f"-> rerun to redo the rest", flush=True)

    return stats()


# An ABSTRACT/SUMMARY *heading* — alone on its line, allowing Marker's markdown
# decoration (## ABSTRACT, **ABSTRACT**). Matching the bare word anywhere would catch
# prose like "Summary table of cost and return analysis…" and quote a table caption
# as the abstract.
ABSTRACT_HEAD = re.compile(r"(?:^|\n)[\s#*_>]*(ABSTRACT|SUMMARY)[\s*_:.\-]*(?:\n|$)", re.I)


# Where an abstract stops: the next front-matter/body heading.
ABSTRACT_END = re.compile(
    r"\n[\s#*_>]*(INTRODUCTION|TABLE OF CONTENTS|CONTENTS|ACKNOWLEDG|CHAPTER|"
    r"LITERATURE (?:CITED|REVIEW)|REVIEW OF LITERATURE|MATERIALS AND METHODS|"
    r"METHODOLOGY|REFERENCES|BIBLIOGRAPHY|LIST OF (?:TABLES|FIGURES))\b",
    re.I,
)


def doc_chunks(tbl, stem: str) -> list[str]:
    """A document's chunks in reading order."""
    safe = stem.replace("'", "''")
    df = tbl.search().where(f"stem = '{safe}'").limit(60).to_pandas()
    if df.empty:
        return []
    return [str(t) for t in df.sort_values(["page", "chunk_index"])["text"].tolist()]


def front_matter(texts: list[str], max_chars: int = 2800) -> str:
    """Title page / author / degree / year — always at the start."""
    return "\n".join(texts[:3])[:max_chars]


def find_abstract(texts: list[str], max_chars: int = 4000) -> str | None:
    """Lift the abstract out of the text VERBATIM.

    Deliberately not asked of the model. Making it retype a 2,000-character abstract cost
    ~17s per document on top of ~3s (49h over the corpus vs ~8h) and invites paraphrasing
    — and it's text we already hold, so copying it is both faster and higher fidelity.

    The abstract is not near the front: in this corpus's thesis files it often sits near
    the END (chunk 16 of 18 in one sampled document), so scan the whole document.
    """
    for i, t in enumerate(texts):
        m = ABSTRACT_HEAD.search(t[:600])
        if not m:
            continue

        body = t[m.end():]
        if i + 1 < len(texts):
            body += "\n" + texts[i + 1]   # abstracts usually spill into the next chunk

        stop = ABSTRACT_END.search(body)
        if stop:
            body = body[: stop.start()]

        body = re.sub(r"\s+", " ", body).strip()
        # Too short to be a real abstract -> probably a stray heading.
        return body[:max_chars] if len(body) > 120 else None

    return None


class BackendDown(RuntimeError):
    """The model endpoint is unusable — stop, don't keep writing junk records."""


def check_backend() -> None:
    """Verify a REAL llama.cpp with a model loaded is on the other end, before doing 8,759 of these.

    Learned the hard way: llama-server died mid-run, Open WebUI (uvicorn) grabbed the
    freed port 8080, and every subsequent request got an HTTP 500 that was recorded as a
    "no-title" document. 5,679 records — 65% of the corpus — were silently garbage.
    Anything answering on the port is not proof the model is there.
    """
    base = LLAMA.rsplit("/v1/", 1)[0]
    try:
        r = requests.get(f"{base}/props", timeout=10)
        r.raise_for_status()
        props = r.json()
    except Exception as e:  # noqa: BLE001
        raise BackendDown(f"No llama.cpp at {base} (/props failed: {type(e).__name__}). "
                          f"Is llama-server running, and is something else on that port?") from e

    if "default_generation_settings" not in props:
        raise BackendDown(f"{base} answered /props but does not look like llama.cpp "
                          f"(got keys: {sorted(props)[:6]}). Something else owns that port.")

    # /props can answer while generation is broken — prove it can actually generate.
    try:
        r = requests.post(LLAMA, json={"messages": [{"role": "user", "content": "Reply with only: OK"}],
                                       "temperature": 0, "max_tokens": 5}, timeout=60)
        r.raise_for_status()
        r.json()["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        raise BackendDown(f"llama.cpp at {base} will not generate ({type(e).__name__}: "
                          f"{str(e)[:120]}). Model loaded?") from e

    model = props.get("model_path") or props.get("model") or "?"
    print(f"backend ok: {base}  model={str(model).split(chr(92))[-1][:60]}", flush=True)


def ask_model(text: str, timeout: int = 180) -> dict | None:
    r = requests.post(
        LLAMA,
        json={
            "messages": [{"role": "user", "content": PROMPT % text}],
            "temperature": 0,
            # Only short fields are generated (the abstract is copied verbatim by
            # find_abstract, not retyped here) — but leave headroom, because a budget
            # that truncates the JSON mid-string loses the whole record.
            "max_tokens": 400,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    out = r.json()["choices"][0]["message"]["content"].strip()
    out = out.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    # The model occasionally wraps the object in a sentence; salvage the braces.
    if not out.startswith("{"):
        m = re.search(r"\{.*\}", out, re.S)
        out = m.group(0) if m else out
    return json.loads(out)


def clean(v):
    """Normalise the model's nulls: it likes the STRING 'None'/'null'/'N/A'."""
    if v is None:
        return None
    s = str(v).strip().strip('"').strip()
    if s.lower() in {"", "none", "null", "n/a", "na", "unknown", "not stated", "not specified"}:
        return None
    return s


VALID_TYPES = {
    "Books", "Serials", "Thesis/Dissertations", "Technical Reports",
    "Investigatory Projects", "Vertical Files", "Non-Prints",
}


def triage(stem: str, meta: dict, text: str) -> tuple[list[str], str | None]:
    """Decide what holds this record back. Empty list = safe to publish."""
    flags: list[str] = []

    title = clean(meta.get("title"))
    if not title or len(title) < 4:
        flags.append("no-title")

    # Restricted: trust the filename, the text, OR the model — any one is enough.
    if (RESTRICTED_PAT.search(stem)
            or RESTRICTED_PAT.search(text[:2000] or "")
            or meta.get("restricted") is True):
        flags.append("restricted")

    if meta.get("is_research") is False:
        flags.append("junk")

    reason = None
    if flags:
        reason = {
            "no-title": "No title could be read from the document",
            "restricted": "Marked restricted / not for open circulation",
            "junk": "Not research output (form, receipt, memo or similar)",
        }[flags[0]]
    return flags, reason


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only process N documents (sample)")
    ap.add_argument("--stats", action="store_true", help="summarise metadata.jsonl and exit")
    ap.add_argument("--purge-errors", action="store_true",
                    help="drop backend-error rows so a rerun retries those documents, then exit")
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)

    if args.stats:
        return stats()

    if args.purge_errors:
        return purge_errors_and_report()

    # Fail fast rather than write thousands of junk records against a dead endpoint.
    try:
        check_backend()
    except BackendDown as e:
        print(f"\nBACKEND UNAVAILABLE\n  {e}\n\nNothing was written. Start llama-server and rerun.")
        return 2

    db = lancedb.connect(str(HERE / "lancedb"))
    tbl = db.open_table("research")

    print(f"index: {tbl.count_rows():,} chunks", flush=True)

    # One pass over the table gives both the document list and each document's last page
    # number. The page count is a FACT we already hold — never ask the model for it.
    tab = tbl.to_arrow()
    last_page: dict[str, int] = {}
    for s, p in zip(tab.column("stem").to_pylist(), tab.column("page").to_pylist()):
        if p is None:
            continue
        if s not in last_page or p > last_page[s]:
            last_page[s] = int(p)
    stems = sorted(last_page.keys())
    del tab
    print(f"documents: {len(stems):,}", flush=True)

    done = load_done()
    todo = [s for s in stems if s not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"already done: {len(done):,}  |  to process: {len(todo):,}")
    if not todo:
        print("nothing to do")
        return 0

    # llama-server runs with --parallel 1 (one slot), so requests are answered one at a
    # time; there is no throughput to gain by threading the client here.
    pdfs = {p.stem: p for p in PDF_DIR.glob("*.pdf")}
    consecutive_errors = 0
    t_start = time.time()

    with OUT_FILE.open("a", encoding="utf-8") as fh:
        for i, stem in enumerate(todo, 1):
            rec = {
                "stem": stem,
                "pdf": str(pdfs[stem]) if stem in pdfs else None,
                "pages": last_page.get(stem),   # from the index, not the model
            }
            text = ""
            try:
                texts = doc_chunks(tbl, stem)
                text = front_matter(texts)
                if not text.strip():
                    rec |= {"flags": ["no-title"], "reason": "No readable text in the index"}
                else:
                    meta = ask_model(text)
                    # Triage reads the whole document's opening, not just what the model saw.
                    flags, reason = triage(stem, meta, "\n".join(texts[:6]))
                    ty = clean(meta.get("type"))
                    rec |= {
                        "title": clean(meta.get("title")),
                        "authors": clean(meta.get("authors")),
                        "responsibility": clean(meta.get("statement_of_responsibility")),
                        "year": (clean(meta.get("year")) or "")[:4] or None,
                        "publisher": clean(meta.get("publisher")),
                        "language": clean(meta.get("language")),
                        "abstract": find_abstract(texts),   # verbatim, not generated
                        "subjects": clean(meta.get("subjects")),
                        "type": ty if ty in VALID_TYPES else "Books",
                        "is_research": meta.get("is_research"),
                        "restricted": meta.get("restricted"),
                        "flags": flags,
                        "reason": reason,
                    }
            except Exception as e:  # noqa: BLE001
                # A backend failure is NOT a property of the document — never let it be
                # recorded as "no-title". Flag it as an error and, if the backend has
                # clearly gone away, stop rather than grinding out thousands of junk rows.
                rec |= {"flags": ["error"], "reason": f"Extraction failed: {type(e).__name__}", "error": str(e)[:200]}

                is_server_err = isinstance(e, (requests.HTTPError, requests.ConnectionError, requests.Timeout))
                consecutive_errors = consecutive_errors + 1 if is_server_err else 0

                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    print(f"\nABORTING: {consecutive_errors} backend failures in a row — "
                          f"the model endpoint looks dead.\n  last error: {str(e)[:160]}\n"
                          f"  {i - consecutive_errors:,} good record(s) this run are saved; "
                          f"fix the backend and rerun to resume.", flush=True)
                    # Don't write this last failed record: leaving it out means the rerun
                    # retries it instead of skipping it as 'done'.
                    return purge_errors_and_report()
            else:
                consecutive_errors = 0

            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()  # crash-safe: the line is on disk before we move on

            if i % 20 == 0 or i == len(todo):
                rate = (time.time() - t_start) / i
                left = (len(todo) - i) * rate
                print(f"  {i:,}/{len(todo):,}  {rate:.1f}s/doc  eta {left/3600:.1f}h", flush=True)

    print(f"\ndone in {(time.time()-t_start)/3600:.2f}h -> {OUT_FILE}")
    return stats()


def stats() -> int:
    if not OUT_FILE.exists():
        print("no metadata.jsonl yet")
        return 1

    total = published = 0
    counts: dict[str, int] = {}
    types: dict[str, int] = {}

    with OUT_FILE.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            total += 1
            fl = r.get("flags") or []
            if fl:
                for f in fl:
                    counts[f] = counts.get(f, 0) + 1
            else:
                published += 1
                types[r.get("type") or "?"] = types.get(r.get("type") or "?", 0) + 1

    print(f"\nextracted     : {total:,}")
    print(f"  -> publish  : {published:,}")
    print(f"  -> on hold  : {total - published:,}")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"       {k:<12}{v:,}")
    print("  types (publishable):")
    for k, v in sorted(types.items(), key=lambda kv: -kv[1]):
        print(f"       {k:<26}{v:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
