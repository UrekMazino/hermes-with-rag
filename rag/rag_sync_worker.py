#!/usr/bin/env python
"""
RAG catalog-sync worker (consumer) — Project Y side of the eLibrary RAG_CATALOG_SYNC_PLAN.md.

Drains the eLibrary MySQL `rag_sync_queue` outbox and keeps the LanceDB "research" index in sync
with catalog materials, keyed by catalog_id:

    action = upsert  -> if the record is published, (re)index it; else ensure it is absent
    action = delete  -> remove the record's chunks

The QUEUE PLUMBING is complete and testable now: claim pending rows, is_published gate, mark
done/error, bounded retries. The heavy embedding step reuses the corpus pipeline (BGE-M3) and is
behind a lazy loader, so `--dry-run` exercises the whole flow end-to-end WITHOUT the model/GPU.

DB creds: read from EL_DB_* env vars, else parsed from the eLibrary .env (single source of truth),
else Laragon defaults. PDFs resolve under <eLibrary>/public/<multimedia.FileLocation>.

--------------------------------------------------------------------------------------------------
INTEGRATION PREREQ (real writes): the existing "research" table was indexed file-keyed
(chunk_id/source/stem) and has no `catalog_id` column. Rows written here carry catalog_id; a
one-time re-index/backfill adds it to legacy corpus rows. Until the column exists, run --dry-run
(delete/upsert log what they would do). A fresh table created by this worker includes catalog_id.
--------------------------------------------------------------------------------------------------

Usage:
    python rag_sync_worker.py --once   --dry-run           # process the pending backlog once
    python rag_sync_worker.py --loop   --interval 30        # keep polling
    python rag_sync_worker.py --catalog-id 10435 --dry-run  # process one id (ad-hoc)
    python rag_sync_worker.py --stats                        # queue depth + counts
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
LANCE_DIR = HERE / "lancedb"
TABLE = "research"

MAX_ATTEMPTS = 5            # give up on an item after this many failures
DEFAULT_BATCH = 200        # rows claimed per poll
CHUNK_CHARS = 1500         # simple splitter size (metadata docs are short; full-text uses the pipeline)
CHUNK_OVERLAP = 200


# ----------------------------------------------------------------------------- config

def _parse_env_file(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def db_config() -> dict:
    """EL_DB_* env vars > eLibrary .env > Laragon defaults."""
    elib_env = Path(os.environ.get("ELIBRARY_ENV", r"C:\laragon\www\elibrary\.env"))
    env = _parse_env_file(elib_env)
    return {
        "host": os.environ.get("EL_DB_HOST", env.get("DB_HOST", "127.0.0.1")),
        "port": int(os.environ.get("EL_DB_PORT", env.get("DB_PORT", "3306"))),
        "user": os.environ.get("EL_DB_USER", env.get("DB_USERNAME", "root")),
        "password": os.environ.get("EL_DB_PASSWORD", env.get("DB_PASSWORD", "")),
        "database": os.environ.get("EL_DB_DATABASE", env.get("DB_DATABASE", "elibrarydb")),
    }


def elibrary_public() -> Path:
    return Path(os.environ.get("ELIBRARY_PUBLIC", r"C:\laragon\www\elibrary\public"))


# ----------------------------------------------------------------------------- MySQL (outbox)

def db_conn():
    import pymysql
    from pymysql.cursors import DictCursor
    cfg = db_config()
    return pymysql.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg["password"], database=cfg["database"],
        cursorclass=DictCursor, autocommit=True, charset="utf8mb4",
    )


def claim_batch(conn, limit: int) -> list[dict]:
    """Oldest pending items still under the retry ceiling."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, catalog_id, action, reason, attempts FROM rag_sync_queue "
            "WHERE processed_at IS NULL AND attempts < %s ORDER BY created_at ASC, id ASC LIMIT %s",
            (MAX_ATTEMPTS, limit),
        )
        return list(cur.fetchall())


def mark_done(conn, item_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE rag_sync_queue SET processed_at = NOW(), error = NULL WHERE id = %s", (item_id,))


def mark_error(conn, item_id: int, err: str) -> None:
    # Bump attempts; leave processed_at NULL so it's retried until MAX_ATTEMPTS.
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE rag_sync_queue SET attempts = attempts + 1, error = %s WHERE id = %s",
            (err[:2000], item_id),
        )


def fetch_catalog(conn, catalog_id: int) -> dict | None:
    """Metadata (view_catalogs) + the full-text PDF path (multimedia), or None if the record is gone."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT catalog_id, is_published, title_statement, summary, general_note, "
            "joint_authors, publisher, pub_year FROM view_catalogs "
            "WHERE catalog_id = %s AND deleted_at IS NULL LIMIT 1",
            (catalog_id,),
        )
        meta = cur.fetchone()
        if not meta:
            return None
        cur.execute(
            "SELECT FileLocation FROM multimedia WHERE catalog_id = %s AND deleted_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (catalog_id,),
        )
        mm = cur.fetchone()

    pdf = None
    if mm and mm.get("FileLocation"):
        loc = str(mm["FileLocation"]).replace("\\", "/").lstrip("/")
        cand = elibrary_public() / loc
        pdf = cand if cand.exists() else None
    meta["pdf_path"] = pdf
    return meta


def queue_stats(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(processed_at IS NULL AND attempts < %s) AS pending, "
            "SUM(processed_at IS NOT NULL) AS done, "
            "SUM(processed_at IS NULL AND attempts >= %s) AS failed "
            "FROM rag_sync_queue",
            (MAX_ATTEMPTS, MAX_ATTEMPTS),
        )
        return cur.fetchone()


def published_ids(conn) -> set[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT catalog_id FROM view_catalogs WHERE is_published = 1 AND deleted_at IS NULL")
        return {int(r["catalog_id"]) for r in cur.fetchall() if r["catalog_id"] is not None}


def indexed_ids() -> set[int]:
    """Distinct non-null catalog_ids currently in LanceDB. Only catalog-synced rows carry a
    catalog_id (corpus rows are NULL), so this filtered scan touches just that subset."""
    import lancedb
    db = lancedb.connect(str(LANCE_DIR))
    if TABLE not in db.table_names():
        return set()
    tbl = db.open_table(TABLE)
    if "catalog_id" not in tbl.schema.names:
        return set()
    rows = tbl.search().where("catalog_id IS NOT NULL").select(["catalog_id"]).limit(100_000_000).to_list()
    return {int(r["catalog_id"]) for r in rows if r.get("catalog_id") is not None}


def enqueue_many(conn, catalog_ids, action: str, reason: str) -> int:
    """Insert outbox rows for the reconciliation drift; deduped against pending rows, chunked."""
    ids = sorted({int(i) for i in catalog_ids if i})
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        fmt = ",".join(["%s"] * len(chunk))
        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM rag_sync_queue WHERE processed_at IS NULL AND catalog_id IN ({fmt})", chunk)
            cur.executemany(
                "INSERT INTO rag_sync_queue (catalog_id, action, reason, attempts, created_at) "
                "VALUES (%s, %s, %s, 0, NOW())",
                [(i, action, reason) for i in chunk],
            )
    return len(ids)


def reconcile(conn, dry_run: bool) -> dict:
    """
    Presence-based drift fix (the nightly backstop): compare published catalog_ids (DB) with what's
    indexed (LanceDB) and enqueue the difference. Catches anything a bulk op missed enqueuing. Content
    edits (same id, changed text) are handled by the normal per-edit enqueue; hash-based re-sync is a
    later refinement.
    """
    pub = published_ids(conn)
    idx = indexed_ids()
    to_index = pub - idx      # should be searchable but isn't
    to_remove = idx - pub     # indexed but no longer published/exists
    print(f"reconcile: published={len(pub)} indexed={len(idx)} "
          f"-> to_index={len(to_index)} to_remove={len(to_remove)}")
    if not dry_run:
        enqueue_many(conn, to_index, "upsert", "reconcile")
        enqueue_many(conn, to_remove, "delete", "reconcile")
        print(f"  enqueued {len(to_index) + len(to_remove)} item(s); run a drain to apply.")
    else:
        print("  [dry-run] no rows enqueued")
    return {"to_index": len(to_index), "to_remove": len(to_remove)}


# ----------------------------------------------------------------------------- document build

def build_document(meta: dict) -> str:
    """The always-available metadata tier: a compact, coherent blob for embedding."""
    parts = []
    if meta.get("title_statement"):
        parts.append(f"Title: {meta['title_statement']}")
    if meta.get("joint_authors"):
        parts.append(f"Author(s): {meta['joint_authors']}")
    pub = " ".join(str(meta.get(k) or "").strip() for k in ("publisher", "pub_year")).strip()
    if pub:
        parts.append(f"Published: {pub}")
    if meta.get("summary"):
        parts.append(f"Summary: {meta['summary']}")
    if meta.get("general_note"):
        parts.append(f"Note: {meta['general_note']}")
    return "\n".join(parts).strip()


def read_fulltext(pdf_path: Path | None) -> str:
    """
    Full-text tier. For born-digital PDFs a quick PyMuPDF pull; scanned PDFs need the OCR batch
    (ocr_run.py) and are left to that pipeline. Returns '' when no usable text (metadata still indexes).
    """
    if not pdf_path:
        return ""
    try:
        import fitz  # PyMuPDF (already a pipeline dep)
        doc = fitz.open(str(pdf_path))
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        return text.strip()
    except Exception:
        return ""   # scanned/needs-OCR or extraction failed -> INTEGRATION: route to ocr_run.py


def simple_chunks(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Self-contained splitter for the scaffold; swap in chunk.py's token-aware splitter for parity."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        i += size - overlap
    return out


# ----------------------------------------------------------------------------- LanceDB

class Embedder:
    """Lazy BGE-M3 (matches embed_index.py). Never loaded in --dry-run."""
    _model = None
    device = None   # None -> auto (cuda if available); set to "cpu" to coexist with a running llama-server

    @classmethod
    def use_model(cls, model) -> None:
        """Adopt an ALREADY-loaded BGE-M3 (same FlagEmbedding API) instead of loading a second
        copy — used by rag_api's /sync/drain, which has one in memory already (~2 GB saved, and
        no 30-60 s load per drain)."""
        cls._model = model

    @classmethod
    def encode(cls, texts: list[str]) -> list[list[float]]:
        if cls._model is None:
            import torch
            from FlagEmbedding import BGEM3FlagModel
            dev = cls.device or ("cuda" if torch.cuda.is_available() else "cpu")
            print(f"  [embedder] loading BGE-M3 on {dev.upper()}", flush=True)
            cls._model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=(dev == "cuda"), device=dev)
        out = cls._model.encode(texts, batch_size=min(len(texts), 32), max_length=512)
        return [v.tolist() for v in out["dense_vecs"]]


def _open_table(dry_run: bool):
    import lancedb
    db = lancedb.connect(str(LANCE_DIR))
    if TABLE not in db.table_names():
        return db, None
    return db, db.open_table(TABLE)


def lance_delete(catalog_id: int, dry_run: bool) -> None:
    if dry_run:
        print(f"  [dry-run] would delete catalog_id={catalog_id} from LanceDB '{TABLE}'")
        return
    _db, tbl = _open_table(dry_run)
    if tbl is None:
        return
    if "catalog_id" not in tbl.schema.names:
        raise RuntimeError("LanceDB table has no 'catalog_id' column yet — see INTEGRATION PREREQ.")
    tbl.delete(f"catalog_id = {int(catalog_id)}")


def lance_upsert(catalog_id: int, chunks: list[str], dry_run: bool) -> int:
    if not chunks:
        # Nothing to index (no metadata + no text) -> make sure any old rows are gone.
        lance_delete(catalog_id, dry_run)
        return 0

    if dry_run:
        print(f"  [dry-run] would upsert catalog_id={catalog_id}: {len(chunks)} chunk(s), "
              f"~{sum(len(c) for c in chunks)} chars")
        return len(chunks)

    import lancedb
    vecs = Embedder.encode(chunks)
    records = [{
        "chunk_id": f"cat-{catalog_id}-{i}",
        "catalog_id": int(catalog_id),
        "source": f"catalog:{catalog_id}",
        "stem": f"catalog-{catalog_id}",
        "page": 0,
        "chunk_index": i,
        "n_tokens": 0,
        "text": chunks[i],
        "vector": vecs[i],
    } for i in range(len(chunks))]

    db = lancedb.connect(str(LANCE_DIR))
    if TABLE not in db.table_names():
        db.create_table(TABLE, data=records)          # fresh table -> schema includes catalog_id
        return len(records)
    tbl = db.open_table(TABLE)
    if "catalog_id" not in tbl.schema.names:
        raise RuntimeError("LanceDB table has no 'catalog_id' column yet — see INTEGRATION PREREQ.")
    tbl.delete(f"catalog_id = {int(catalog_id)}")       # idempotent replace
    tbl.add(records)
    return len(records)


# ----------------------------------------------------------------------------- processing

def process_item(conn, item: dict, dry_run: bool) -> str:
    """Returns a short status string. Raises on failure (caller marks the error + retries)."""
    catalog_id = int(item["catalog_id"])
    action = item["action"]

    if action == "delete":
        lance_delete(catalog_id, dry_run)
        return f"deleted catalog_id={catalog_id}"

    # action == upsert : sync to current state (is_published gate lives here)
    meta = fetch_catalog(conn, catalog_id)
    if meta is None or int(meta.get("is_published") or 0) != 1:
        lance_delete(catalog_id, dry_run)
        why = "gone" if meta is None else "on-hold"
        return f"ensured-absent catalog_id={catalog_id} ({why})"

    doc = build_document(meta)
    full = read_fulltext(meta.get("pdf_path"))
    chunks = simple_chunks(doc) + simple_chunks(full)
    n = lance_upsert(catalog_id, chunks, dry_run)
    tier = "metadata+fulltext" if full else "metadata-only"
    return f"upserted catalog_id={catalog_id}: {n} chunk(s) [{tier}]"


def drain_once(conn, limit: int, dry_run: bool) -> int:
    items = claim_batch(conn, limit)
    if not items:
        return 0
    for item in items:
        try:
            status = process_item(conn, item, dry_run)
            mark_done(conn, item["id"])
            print(f"  ok   #{item['id']} {item['action']}/{item['reason']} -> {status}")
        except Exception as e:
            mark_error(conn, item["id"], f"{type(e).__name__}: {e}")
            print(f"  ERR  #{item['id']} catalog_id={item['catalog_id']}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            if os.environ.get("RAG_SYNC_DEBUG"):
                traceback.print_exc()
    return len(items)


# ----------------------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser(description="RAG catalog-sync worker (drains rag_sync_queue -> LanceDB)")
    ap.add_argument("--once", action="store_true", help="process the current backlog once, then exit")
    ap.add_argument("--loop", action="store_true", help="poll forever")
    ap.add_argument("--interval", type=int, default=30, help="poll interval seconds (--loop)")
    ap.add_argument("--limit", type=int, default=DEFAULT_BATCH, help="rows claimed per poll")
    ap.add_argument("--catalog-id", type=int, help="process just this catalog_id (ad-hoc upsert)")
    ap.add_argument("--dry-run", action="store_true", help="log actions; never load the model or write LanceDB")
    ap.add_argument("--cpu", action="store_true", help="embed on CPU (coexist with a running llama-server)")
    ap.add_argument("--reconcile", action="store_true", help="diff published (DB) vs indexed (LanceDB), enqueue the drift")
    ap.add_argument("--stats", action="store_true", help="print queue depth + counts, then exit")
    args = ap.parse_args()

    if args.cpu:
        Embedder.device = "cpu"

    conn = db_conn()

    if args.stats:
        s = queue_stats(conn)
        print(f"rag_sync_queue: total={s['total']} pending={s['pending'] or 0} "
              f"done={s['done'] or 0} failed={s['failed'] or 0}")
        return 0

    if args.reconcile:
        reconcile(conn, args.dry_run)
        return 0

    if args.catalog_id:
        item = {"id": 0, "catalog_id": args.catalog_id, "action": "upsert", "reason": "manual", "attempts": 0}
        try:
            print(process_item(conn, item, args.dry_run))
            return 0
        except Exception as e:
            print(f"ERR: {type(e).__name__}: {e}", file=sys.stderr)
            return 1

    if args.once:
        n = drain_once(conn, args.limit, args.dry_run)
        print(f"processed {n} item(s)")
        return 0

    if args.loop:
        print(f"polling rag_sync_queue every {args.interval}s (limit={args.limit}, dry_run={args.dry_run})...")
        while True:
            try:
                n = drain_once(conn, args.limit, args.dry_run)
                if n:
                    print(f"  drained {n}")
            except Exception as e:
                print(f"poll error: {type(e).__name__}: {e}", file=sys.stderr)
                conn = db_conn()   # reconnect on a dropped connection
            time.sleep(args.interval)

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
