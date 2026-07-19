"""
Stage 2.2 / 3c — Chunk extracted text OR OCR markdown into retrieval units.

Two sources:
  --source extracted  (default)  -> stage2/extracted/*.json  (Stage 2 TEXT_READY)
                                     -> stage2/chunks.jsonl
  --source ocr        -> ocr_out/*.md  (Stage 3 Marker markdown, paginated)
                                     -> stage3/chunks_ocr.jsonl

Both split per page (page-accurate citations) into token-sized, overlapping chunks
using the BGE-M3 tokenizer for length (matches the embedder). OCR markdown is split
on Marker's page separator  `\\n\\n{PAGE_ID}` + ("-"*48) + `\\n\\n`  (PAGE_ID 0-indexed),
then chunked with markdown-aware separators.

A **garble filter** drops chunks that are clearly OCR junk (consonant-soup), without
dropping number/table-heavy chunks. Tunable; prints how many it drops.

    .\\.venv\\Scripts\\python.exe .\\chunk.py --source extracted
    .\\.venv\\Scripts\\python.exe .\\chunk.py --source ocr
CPU-only.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from progress_modal import ProgressModal
except Exception:
    ProgressModal = None

HERE = Path(__file__).resolve().parent
TOKENIZER = "BAAI/bge-m3"

SOURCES = {
    "extracted": {"dir": HERE / "stage2" / "extracted", "glob": "*.json",
                  "out": HERE / "stage2" / "chunks.jsonl"},
    "ocr":       {"dir": HERE / "ocr_out", "glob": "*.md",
                  "out": HERE / "stage3" / "chunks_ocr.jsonl"},
}

# Marker paginated page separator: "\n\n{PAGE_ID}" + "-"*48 + "\n\n" (PAGE_ID 0-indexed)
PAGE_SEP = re.compile(r"\{(\d+)\}-{20,}")
_VOWEL = re.compile(r"[aeiouAEIOU]")
_ALPHA = re.compile(r"[A-Za-z]{3,}")


def split_pages_md(md: str):
    """Yield (page_number, text) using Marker's page separators; fallback = whole doc as page 1."""
    parts = PAGE_SEP.split(md)
    if len(parts) == 1:               # un-paginated markdown
        yield 1, md
        return
    # parts = [pre, pageid, text, pageid, text, ...]
    pre = parts[0].strip()
    if pre:                            # content before the first marker -> page 1
        yield 1, pre
    for i in range(1, len(parts) - 1, 2):
        try:
            pno = int(parts[i]) + 1    # PAGE_ID is 0-indexed
        except Exception:
            pno = 1
        yield pno, parts[i + 1]


def is_garbled(text: str, min_alpha: int, max_novowel: float) -> bool:
    """True if a chunk looks like OCR junk: many 3+ letter tokens with no vowel.
    Keeps number/table/symbol-heavy chunks (few alpha tokens -> not judged)."""
    alpha = _ALPHA.findall(text)
    if len(alpha) < min_alpha:
        return False
    novowel = sum(1 for t in alpha if not _VOWEL.search(t))
    return (novowel / len(alpha)) > max_novowel


def main() -> int:
    ap = argparse.ArgumentParser(description="Chunk text/markdown (token-aware) + garble filter")
    ap.add_argument("--source", choices=list(SOURCES), default="extracted")
    ap.add_argument("--chunk-size", type=int, default=512, help="target tokens per chunk")
    ap.add_argument("--overlap", type=int, default=80, help="token overlap (~15%)")
    ap.add_argument("--min-alpha", type=int, default=12, help="garble filter: min 3+letter tokens to judge")
    ap.add_argument("--max-novowel", type=float, default=0.45, help="garble filter: max fraction of no-vowel tokens")
    ap.add_argument("--no-filter", action="store_true", help="disable the garble filter")
    args = ap.parse_args()

    src = SOURCES[args.source]
    docs = sorted(src["dir"].glob(src["glob"]))
    if not docs:
        print(f"ERR: no {args.source} files in {src['dir']}.", file=sys.stderr)
        return 2

    # De-dup: exclude redundant duplicate copies (from dedupe_ocr.py) so identical
    # content isn't indexed multiple times.
    skip_stems = set()
    skip_file = HERE / "stage3" / "ocr_dupe_skip.txt"
    if args.source == "ocr" and skip_file.exists():
        skip_stems = {ln.strip() for ln in skip_file.read_text(encoding="utf-8").splitlines() if ln.strip()}
        before = len(docs)
        docs = [d for d in docs if d.stem not in skip_stems]
        print(f"De-dup: excluded {before - len(docs)} duplicate .md (skip-list: {len(skip_stems)} stems).")

    from transformers import AutoTokenizer
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    print(f"Loading BGE-M3 tokenizer ({TOKENIZER})...", flush=True)
    tok = AutoTokenizer.from_pretrained(TOKENIZER)

    def ntok(s: str) -> int:
        return len(tok.encode(s, add_special_tokens=False))

    # markdown-aware separators for OCR; plain for extracted text
    seps = (["\n## ", "\n### ", "\n#### ", "\n\n", "\n", ". ", "; ", " ", ""]
            if args.source == "ocr" else ["\n\n", "\n", ". ", "; ", " ", ""])
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=args.chunk_size, chunk_overlap=args.overlap,
        length_function=ntok, separators=seps)

    out_path = src["out"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Chunking {len(docs)} {args.source} docs -> {out_path}  "
          f"(size={args.chunk_size} tok, overlap={args.overlap}, filter={'off' if args.no_filter else 'on'})\n")
    pm = ProgressModal(f"Chunking ({args.source})", len(docs), "BGE-M3 tokens") if ProgressModal else None

    n_chunks = tok_sum = dropped = 0
    with out_path.open("w", encoding="utf-8") as out:
        for i, dp in enumerate(docs, 1):
            if pm:
                pm.update(i - 1, dp.stem)
                if pm.cancelled:
                    print("\nCancelled via progress window.")
                    break
            stem = dp.stem
            if args.source == "extracted":
                rec = json.loads(dp.read_text(encoding="utf-8"))
                pages = [(p["page"], p["text"]) for p in rec["pages"]]
                source = rec.get("source", stem)
            else:
                pages = list(split_pages_md(dp.read_text(encoding="utf-8")))
                source = str(dp)
            for pno, ptext in pages:
                for ci, chunk in enumerate(splitter.split_text(ptext)):
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    if not args.no_filter and is_garbled(chunk, args.min_alpha, args.max_novowel):
                        dropped += 1
                        continue
                    nt = ntok(chunk)
                    row = {"chunk_id": f"{stem}__p{pno}__c{ci}",
                           "source": source, "stem": stem, "page": pno,
                           "chunk_index": ci, "n_tokens": nt, "text": chunk}
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    n_chunks += 1
                    tok_sum += nt

    if pm:
        pm.close("Chunking complete")
    print("\n" + "=" * 70)
    print("CHUNKING DONE")
    print("=" * 70)
    print(f"  chunks: {n_chunks:,}   avg tokens: {tok_sum/max(n_chunks,1):.0f}   garble-dropped: {dropped:,}")
    print(f"  output: {out_path}")
    print(f"  next: embed_index.py --chunks {out_path.name} "
          f"--mode {'append' if args.source=='ocr' else 'overwrite'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
