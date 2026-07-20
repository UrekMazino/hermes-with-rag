# Manual Re-index — applying pending AI sync items

**Use this when:** the eLibrary **AI Sync Monitor** (Administration → AI Sync Monitor) shows rows stuck
at **Pending**, or you clicked **Re-sync** / **Re-index for AI** and nothing changed.

> **The one thing to understand:** the *Re-sync* button only **queues** the work. A separate **worker**
> (here in Project Y) is what actually re-indexes into LanceDB. **Nothing drains the queue
> automatically** — until you run the worker, items stay *Pending*. That's expected, not a bug.
>
> ```
> eLibrary "Re-sync"  ──▶  rag_sync_queue (MySQL)  ──▶  rag_sync_worker.py  ──▶  LanceDB
>      (queues)                  (waiting)               (YOU run this)         (searchable)
> ```

---

## Before you start

- **MySQL / Laragon must be running** — the queue lives in the eLibrary database.
- **llama-server is NOT required.** The worker embeds locally with BGE-M3; it never calls the LLM.
  (llama-server is only needed to *answer* questions, via `rag_api`.)
- If llama-server **is** running, add `--cpu` so the worker doesn't fight it for VRAM.

---

## Steps

**1. Open PowerShell in the RAG folder**
```powershell
cd C:\Users\jcvia\PyCharmMiscProject\ProjectY\rag
```

**2. See what's waiting**
```powershell
.\.venv\Scripts\python.exe rag_sync_worker.py --stats
```
```
rag_sync_queue: total=2 pending=2 done=0 failed=0
```

**3. Drain the queue — this is the actual re-index**
```powershell
.\.venv\Scripts\python.exe rag_sync_worker.py --once --cpu
```
- `--once` — process the backlog, then exit.
- `--cpu`  — embed on CPU so it coexists with a running llama-server. A handful of metadata records
  takes seconds.

You'll get one line per item:
```
ok   #12 upsert/manual -> upserted catalog_id=10435: 2 chunk(s) [metadata-only]
processed 2 item(s)
```

**4. Verify**
```powershell
.\.venv\Scripts\python.exe rag_sync_worker.py --stats     # pending should be 0
```
Refresh the **AI Sync Monitor** — the rows flip **Pending → Synced**, and the record's View page badge
becomes **AI-indexed**.

---

## Variations

| Situation | Command |
|---|---|
| One specific record (e.g. after fixing its OCR) | `... rag_sync_worker.py --catalog-id 10435 --cpu` |
| Keep it draining by itself (recommended) | `... rag_sync_worker.py --loop --interval 30 --cpu` |
| Big batch / PDF-OCR heavy | stop llama-server, then run **without** `--cpu` (uses the GPU) |
| Suspect drift (bulk ops, things changed outside the app) | `... --reconcile` then `... --once --cpu` |
| Preview without touching anything | add `--dry-run` |

---

## What to expect

- **`[metadata-only]`** on a record whose PDF is **scanned** is normal — the title/summary/subjects are
  indexed, but the full text needs the OCR batch (`ocr_run.py`). Not an error.
- **`[metadata+fulltext]`** means the PDF had an extractable text layer and its content is indexed too.
- An **on-hold / unpublished** record is intentionally *not* indexed — the worker removes it instead
  (`ensured-absent … (on-hold)`). Only published records are searchable by the AI.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Items stay **Pending** after running | You ran it in the wrong folder, or used the system Python. Use `.\.venv\Scripts\python.exe` from `…\ProjectY\rag`. |
| **Failed** rows in the monitor | A row gives up after 5 attempts. Read the cause: `SELECT catalog_id, error FROM rag_sync_queue WHERE processed_at IS NULL AND attempts >= 5;` then hit **Re-sync** (it resets the attempts). |
| `No module named 'pymysql'` | `uv pip install --python .venv\Scripts\python.exe -r requirements.txt` |
| `LanceDB table has no 'catalog_id' column` | one-time: `.\.venv\Scripts\python.exe lance_add_catalog_id.py` |
| Can't connect to MySQL | Laragon/MySQL isn't running, or `ELIBRARY_ENV` doesn't point at the eLibrary `.env`. |
| VRAM error / OOM | add `--cpu`, or stop llama-server for the batch. |

---

## Stop doing this by hand

Running `--once` after every edit gets old. Two options:

- **Leave a drain loop running:** `.\.venv\Scripts\python.exe rag_sync_worker.py --loop --interval 30 --cpu`
- **Register a scheduled task** so it survives reboots — same pattern as `register-ocr-task.ps1`
  (see `_setup/register_logon_tasks.ps1`). Nothing is registered today, which is why the queue only
  moves when you run it.

Pair it with a **nightly** `--reconcile` + drain to catch anything that slipped through.

---

**See also:** `CATALOG_SYNC_RUNBOOK.md` (full worker reference: setup, all flags, scheduling, GPU
contention) · eLibrary `RAG_CATALOG_SYNC_PLAN.md` (why the outbox design) ·
`STARTING_HERMES_GUIDE.md` (starting llama-server / the RAG API after a reboot).
