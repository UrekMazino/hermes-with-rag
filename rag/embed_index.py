"""
Stage 2.3 — Embed chunks with BGE-M3 and build the LanceDB index.

Reads `stage2/chunks.jsonl`, embeds each chunk's text with **BGE-M3** (dense,
1024-d) and writes text + metadata + vector into a LanceDB table at
`rag/lancedb` (table `research`). Then builds a full-text (Tantivy) index on the
text so retrieval can combine vector + keyword (hybrid comes fully online in
Stage 4 with sparse + rerank).

GPU note: BGE-M3 runs on CUDA if available. **Stop llama-server first** for a
clean batch (frees VRAM, avoids an OOM if the server loads its model mid-run):
    Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force

    cd C:\\Users\\jcvia\\PyCharmMiscProject\\ProjectY\\rag
    .\\.venv\\Scripts\\python.exe .\\embed_index.py [--batch 64] [--table research]

Idempotent: rewrites the table from scratch (mode="overwrite").
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

# Windows without Developer Mode can't create the HF cache symlinks -> copy instead.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

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
CHUNKS = HERE / "stage2" / "chunks.jsonl"
LANCE_DIR = HERE / "lancedb"
DIM = 1024  # BGE-M3 dense dimension


def load_chunks(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Embed chunks (BGE-M3) -> LanceDB")
    ap.add_argument("--batch", type=int, default=64, help="embedding batch size")
    ap.add_argument("--table", default="research", help="LanceDB table name")
    ap.add_argument("--chunks", default=None,
                    help="chunks jsonl path (default stage2/chunks.jsonl); relative resolves under rag/")
    ap.add_argument("--mode", choices=["overwrite", "append"], default="overwrite",
                    help="overwrite rebuilds the table; append adds to the existing table (Stage 3 OCR)")
    ap.add_argument("--dedupe-ocr", action="store_true",
                    help="append mode: first delete existing OCR rows (source LIKE '%ocr_out%') so "
                         "re-running is idempotent (no duplicate chunks)")
    args = ap.parse_args()

    chunks_path = Path(args.chunks) if args.chunks else CHUNKS
    if not chunks_path.is_absolute():
        chunks_path = HERE / chunks_path
    if not chunks_path.exists():
        print(f"ERR: no chunks at {chunks_path}. Run chunk.py first.", file=sys.stderr)
        return 2
    rows = load_chunks(chunks_path)
    if not rows:
        print("No chunks to embed.", file=sys.stderr)
        return 1

    import torch
    from FlagEmbedding import BGEM3FlagModel
    import lancedb

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Embedding {len(rows):,} chunks ({args.mode}) with BGE-M3 on {dev.upper()} "
          f"(batch={args.batch})...", flush=True)
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=(dev == "cuda"), device=dev)

    pm = ProgressModal(f"BGE-M3 embedding ({args.mode})", len(rows), f"device={dev}") if ProgressModal else None

    db = lancedb.connect(str(LANCE_DIR))
    tbl = None
    if args.mode == "append":
        if args.table not in db.table_names():
            print(f"ERR: table '{args.table}' doesn't exist; run --mode overwrite first.", file=sys.stderr)
            return 2
        tbl = db.open_table(args.table)
        print(f"Append mode: existing rows = {tbl.count_rows():,}")
        if args.dedupe_ocr:
            try:
                tbl.delete("source LIKE '%ocr_out%'")
                print(f"  deduped: dropped existing OCR rows -> {tbl.count_rows():,} rows remain")
            except Exception as e:
                print(f"  dedupe note: {type(e).__name__}: {e}")
    texts = [r["text"] for r in rows]
    records = []
    first = True
    done = 0
    B = args.batch
    for start in range(0, len(rows), B):
        batch_rows = rows[start:start + B]
        batch_txt = texts[start:start + B]
        out = model.encode(batch_txt, batch_size=len(batch_txt), max_length=512)
        vecs = out["dense_vecs"]
        for r, v in zip(batch_rows, vecs):
            records.append({
                "chunk_id": r["chunk_id"], "source": r["source"], "stem": r["stem"],
                "page": int(r["page"]), "chunk_index": int(r["chunk_index"]),
                "n_tokens": int(r["n_tokens"]), "text": r["text"],
                "vector": v.tolist(),
            })
        done += len(batch_rows)
        # flush to LanceDB periodically to bound memory
        if len(records) >= 2000 or done >= len(rows):
            if args.mode == "overwrite" and first:
                tbl = db.create_table(args.table, data=records, mode="overwrite")
                first = False
            else:
                tbl.add(records)  # append mode (pre-opened) or subsequent overwrite batches
            records = []
        if pm:
            pm.update(done, f"{done:,}/{len(rows):,} embedded")
            if pm.cancelled:
                print("\nCancelled via progress window.")
                break

    if pm:
        pm.close("Embedding complete")

    if tbl is None:
        print("No table written.", file=sys.stderr)
        return 1

    n = tbl.count_rows()
    print(f"\nLanceDB table '{args.table}': {n:,} rows at {LANCE_DIR}")
    print("Building full-text (Tantivy) index on 'text'...", flush=True)
    try:
        tbl.create_fts_index("text", replace=True)
        print("  FTS index OK")
    except Exception as e:
        print(f"  FTS index note: {type(e).__name__}: {e}")
    # NOTE: deliberately NO IVF-PQ vector index at this scale. Exact (brute-force)
    # search is ~80 ms at 52k-250k rows, gives 100% recall AND full-precision scores;
    # the IVF-PQ index was missing sparse matches (e.g. a 2-chunk OCR doc) and degraded
    # scores via quantization. Revisit only past ~1M chunks.

    print("\n" + "=" * 70)
    print("EMBED + INDEX DONE")
    print("=" * 70)
    print(f"  rows: {n:,}   dim: {DIM}   table: {args.table}")
    print(f"  next: rewire rag_mcp_server.py search_docs -> LanceDB, restart gateway")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
