#!/usr/bin/env python
"""
One-time (idempotent): add a nullable `catalog_id` column to the LanceDB "research" table so the
catalog-sync worker (rag_sync_worker.py) can upsert/delete catalog-sourced chunks by catalog_id.

Existing corpus rows were indexed file-keyed (chunk_id/source/stem) and get catalog_id = NULL;
rows the worker writes carry their real catalog_id. See RAG_CATALOG_SYNC_PLAN.md.
"""
from pathlib import Path

import lancedb

HERE = Path(__file__).resolve().parent
TABLE = "research"

db = lancedb.connect(str(HERE / "lancedb"))

if TABLE not in db.table_names():
    print(f"no '{TABLE}' table yet — the worker will create it WITH catalog_id on first write.")
    raise SystemExit(0)

tbl = db.open_table(TABLE)
if "catalog_id" in tbl.schema.names:
    print(f"catalog_id already present ({tbl.count_rows():,} rows) — nothing to do.")
    raise SystemExit(0)

print(f"adding catalog_id to '{TABLE}' ({tbl.count_rows():,} rows; corpus rows -> NULL)...")
tbl.add_columns({"catalog_id": "CAST(NULL AS BIGINT)"})
print("done. schema:", tbl.schema.names)
