# Catalog Sync Runbook — feeding live eLibrary changes into the RAG index

Operational guide for the **catalog-sync worker** (`rag/rag_sync_worker.py`): it keeps the LanceDB
`research` index in step with the smart-eLibrary catalog, so Hermes (`search_docs`) answers over
newly created / edited / deleted / (un)published materials.

Design rationale + the full plan live in the eLibrary repo: **`RAG_CATALOG_SYNC_PLAN.md`**. This
file is the *how-to-run-it* companion.

> **Just need to clear "Pending" rows in the AI Sync Monitor?** Use the short task guide:
> **[`MANUAL_REINDEX_PROCEDURE.md`](MANUAL_REINDEX_PROCEDURE.md)**. This runbook is the full reference.

---

## How it works (30-second version)

```
eLibrary (Laravel)                     shared MySQL              Project Y (this repo)
─────────────────                      ────────────             ─────────────────────
create/edit/delete/publish/import  ──▶  rag_sync_queue   ──▶  rag_sync_worker.py  ──▶  LanceDB
  (App\Services\RagSync enqueues)        (outbox table)         (drains, gates,          "research"
                                                                 embeds, upserts)         (catalog_id)
```

- **Producer** (eLibrary, already deployed): every catalog mutation writes a row to `rag_sync_queue`
  (an outbox table in the eLibrary MySQL). `UPSERT` = "sync to current state", `DELETE` = "ensure
  absent". It never blocks a save and is deduped to one pending row per `catalog_id`.
- **Consumer** (this worker): polls `rag_sync_queue`, resolves each `catalog_id` to metadata
  (`view_catalogs`) + its full-text PDF (`multimedia` → `public/`), and upserts/deletes chunks in
  LanceDB keyed by `catalog_id`. `is_published` is the gate: only published records are searchable.

---

## One-time setup (fresh machine)

1. **RAG venv + deps.** The worker needs `pymysql` (and the corpus stack: `FlagEmbedding`, `lancedb`,
   `pymupdf`). All are pinned in `rag/requirements.txt`:
   ```bash
   cd rag
   uv venv .venv                        # if not already created
   uv pip install --python .venv/Scripts/python.exe -r requirements.txt
   ```
   (The venv is uv-managed and has no `pip`; always go through `uv pip …`.)

2. **Add the `catalog_id` column to LanceDB (one-time, idempotent).** The corpus was indexed
   file-keyed; the worker needs a `catalog_id` column to upsert/delete by record:
   ```bash
   .venv/Scripts/python.exe lance_add_catalog_id.py
   ```
   Existing corpus rows get `catalog_id = NULL`; the worker's rows carry their id. Safe to re-run.

3. **Database credentials.** The worker reads them, in this order:
   `EL_DB_*` env vars → the eLibrary `.env` → Laragon defaults (`127.0.0.1 / root / elibrarydb`).
   By default it parses `C:\laragon\www\elibrary\.env` — set `ELIBRARY_ENV` if the eLibrary lives
   elsewhere, and `ELIBRARY_PUBLIC` for the PDF root (default `C:\laragon\www\elibrary\public`).
   > The eLibrary side owns `rag_sync_queue` (its migration creates it) — no schema step here.

---

## Running it

All commands run from `rag/` with the venv python (`.venv/Scripts/python.exe`).

```bash
# queue depth + counts
python rag_sync_worker.py --stats

# process the current backlog once, then exit
python rag_sync_worker.py --once

# ...on CPU, to coexist with a running llama-server (no VRAM contention)
python rag_sync_worker.py --once --cpu

# keep polling (a long-running service)
python rag_sync_worker.py --loop --interval 30 --cpu

# process one record ad-hoc (handy after fixing its OCR)
python rag_sync_worker.py --catalog-id 10435

# dry-run: log what it WOULD do; never loads the model or writes LanceDB
python rag_sync_worker.py --catalog-id 10435 --dry-run
```

**Flags:** `--once` / `--loop` (`--interval N`) / `--catalog-id N` / `--stats` / `--dry-run` /
`--cpu` / `--reconcile` / `--limit N`.

---

## The nightly reconciliation (drift backstop)

Bulk operations or a missed event can leave the index out of step. `--reconcile` diffs **published**
catalog_ids (DB) against **indexed** ones (LanceDB) and enqueues the difference; a drain then applies
it:

```bash
python rag_sync_worker.py --reconcile          # detect drift + enqueue it (reason=reconcile)
python rag_sync_worker.py --once --cpu         # apply it
# (preview only:)  python rag_sync_worker.py --reconcile --dry-run
```

Schedule this nightly. It also doubles as the **initial backfill**: on a fresh index, `--reconcile`
enqueues every published record, and the drain indexes them.

---

## GPU contention (important on the single 4080)

The serving llama-server uses ~13.5 GB of 16 GB. So:

- **Small metadata edits** → `--cpu` (BGE-M3 on one short doc is sub-second) — safe while serving.
- **Large batches / OCR-heavy full-text** → run in the **GPU window with llama-server stopped**
  (see `start-llama-server.ps1` / `STARTING_HERMES_GUIDE.md` to stop it), then restart it after.

---

## Draining from the app ("Run sync now")

`rag_api` exposes **`POST /sync/drain`** (shared-token auth, same as `/agent/answer`), which the
eLibrary **AI Sync Monitor**'s *Run sync now* button calls server-side. The gateway does the work
because it already holds BGE-M3 + LanceDB — no model load, no `exec()` from PHP.

It is **bounded** (`DRAIN_LIMIT` 25, max 200): embedding shares the single model instance with
search, so an unbounded drain would stall AI-mode queries. The UI simply repeats while
`remaining > 0`. Response: `{processed, failed, remaining, took_ms}`. After a drain it clears the
table-refresh TTL so the very next search sees what was just indexed.

## Scheduling (Windows Task Scheduler)

**`register-projecty-tasks.ps1`** registers three staggered logon tasks (run it **elevated**, once):

| Task | Delay | What |
|---|---|---|
| `ProjectY-llama-server` | +15s | the model on :8080 |
| `ProjectY-rag-api` | +60s | AI-mode gateway on :8090 |
| `ProjectY-sync-worker` | +90s | `start-sync-worker.ps1` → `--loop --interval 30 --cpu` |

With those in place the queue drains itself every 30 s and AI mode survives a reboot. Add a
**nightly reconcile + drain** (`--reconcile` then `--once`) in the GPU window as the drift backstop.
The Discord gateway is registered separately by `_setup\register_gateway_task.ps1`.

---

## Verify

```bash
python rag_sync_worker.py --stats     # pending should trend to 0 after a drain
```
Then, on the eLibrary side, open a published record's **View** page — the **AI-index badge** should
read *AI-indexed* (it reflects the outbox state). Ask Hermes a question answerable only from that
record and confirm it's cited.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No module named 'pymysql'` | `uv pip install --python .venv/Scripts/python.exe -r requirements.txt` |
| `LanceDB table has no 'catalog_id' column` | run `python lance_add_catalog_id.py` (step 2) |
| Can't connect to MySQL | check `EL_DB_*` env or that `ELIBRARY_ENV` points at the right `.env`; is MySQL/Laragon up? |
| Items stuck (never drain) | `--stats` shows `failed` once a row hits 5 attempts; inspect `rag_sync_queue.error` for the cause |
| Record shows "metadata-only" | its PDF is scanned → needs the OCR batch (`ocr_run.py`); metadata still indexes |
| VRAM OOM while serving | use `--cpu`, or stop llama-server for the batch |

---

## Files

- `rag/rag_sync_worker.py` — the worker (producer lives in eLibrary: `app/Services/RagSync.php`).
- `rag/lance_add_catalog_id.py` — the one-time LanceDB column migration.
- eLibrary `RAG_CATALOG_SYNC_PLAN.md` — design + what's built + open decisions.
