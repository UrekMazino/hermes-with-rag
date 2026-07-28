# Project Y — Deployment Runbook (soup-to-nuts)

**The single entry point.** This sequences a full rebuild — from a bare machine to the current
phase (Hermes + local RAG + the smart-eLibrary catalog integration) — and links the detailed
per-phase docs. Read this first; follow the links for step detail.

> **Two repos, one stack.**
> - **`hermes-with-rag`** (this repo, `C:\Users\jcvia\PyCharmMiscProject\ProjectY`) — the AI server:
>   Hermes agent, llama.cpp serving, the RAG pipeline + index, and the catalog-sync worker.
> - **`smart-elibrary`** (`github.com/UrekMazino/smart-elibrary`, `C:\laragon\www\elibrary`) — the
>   Laravel ILS/OPAC that produces catalog data and the RAG-sync queue.
>
> They share **one MySQL** (`elibrarydb`) and the eLibrary **`.env`** (source of `HERMES_GATEWAY_TOKEN`
> + DB creds). The current target is **Windows 11 + Laragon + WSL2** on one **RTX 4080 Super (16 GB)**;
> `SERVER_SPEC.md` covers the future dedicated Linux server.

---

## 0. Prerequisites (install once)

- **Windows 11**, **NVIDIA driver + CUDA** for the RTX 4080 Super.
- **WSL2** (Hermes runs here) + a Linux distro.
- **[uv](https://docs.astral.sh/uv/)** (Python venv/dependency manager — used for both Hermes and the RAG venv).
- **Git**, **Python 3.11+**.
- **Laragon** (bundles **MySQL/MariaDB**, PHP 8.3, Apache) for the eLibrary.
- **Composer** (PHP deps), **Node.js** (front-end assets), **Meilisearch** + **Docker Desktop**
  (OPAC search — see `smart-elibrary/MEILISEARCH_SETUP.md`).

Clone both repos:
```bash
git clone https://github.com/UrekMazino/hermes-with-rag.git   # -> C:\Users\jcvia\PyCharmMiscProject\ProjectY
git clone https://github.com/UrekMazino/smart-elibrary.git    # -> C:\laragon\www\elibrary
```

---

## 1. Hermes agent  → `STARTING_HERMES_GUIDE.md`

Install the Hermes agent in **WSL2** (uses `uv`). The vendored installer is `_setup/hermes_install.sh`
(the upstream one-liner is `curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`);
`_setup/run_hermes_install.sh` shows the exact non-interactive invocation used here.

Detail + post-reboot start/stop/test: **`STARTING_HERMES_GUIDE.md`**.

## 2. llama.cpp serving + model  → `README.md`, `SERVER_SPEC.md`

Download the GGUF model into `models/`, then serve it with **`start-llama-server.ps1`** (binds
`127.0.0.1:8080`). Model choice + key flags are in **`README.md` → "Model and Key Server Flags"**;
the future dedicated inference server (Llama-3.3-70B, vLLM) is **`SERVER_SPEC.md`**.

> `models/`, `llama-cpp/` and `_downloads/` are **gitignored** — download them on each machine.

## 3. Gateway + RAG API + proxy  (`start-*.ps1`)

Bring up the service front:
- **`start-hermes-gateway.ps1`** — the Hermes gateway (agent transport).
- **`start-rag-api.ps1`** — the FastAPI in front of RAG retrieval; requires **`HERMES_GATEWAY_TOKEN`**
  (read from env or the eLibrary `.env`) and binds `127.0.0.1`.
- **`start-nginx-proxy.ps1`** — the reverse proxy.
- **`register-ocr-task.ps1`** / `_setup/register_gateway_task.ps1` / `_setup/register_logon_tasks.ps1`
  — Task Scheduler registration so these come back after a reboot.

## 4. Security lockdown  → `HERMES_SECURITY_LOCKDOWN_2026-07-09.md`

Apply the dedicated **`locked-rag`** profile (corpus-search only) — this is the real fix for the
prompt-extraction leaks. Includes the migration gotchas (empty the profile's `skills/` dir, pairing
isn't cloned, etc.). **Do not skip** for any internet-facing deployment.

## 5. RAG corpus pipeline  → `rag/README.md`, `rag/RAG_PLAN_AND_PROGRESS.md`

Create the RAG venv and install pinned deps, then build the index:
```bash
cd rag
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
```
Then triage → OCR (Marker/Surya) → chunk → embed (BGE-M3) → **LanceDB** (`rag/lancedb/`, table
`research`). Full pipeline + stages + test questions: **`rag/README.md`**,
**`rag/RAG_PLAN_AND_PROGRESS.md`**, `rag/RAG_TEST_QUESTIONS.md`.

> `rag/pdf/` (corpus), `rag/lancedb/` (index), `rag/.venv/` and all stage outputs are **gitignored** —
> the corpus + index are rebuilt/copied per machine, not cloned.

The **`search_docs` MCP tool** (`rag/rag_mcp_server.py`) is how Hermes queries the index — register
it in Hermes (see `rag/README.md → "Manage the MCP tool"`).

## 6. eLibrary (Laravel ILS)  → `smart-elibrary/` docs

In `C:\laragon\www\elibrary`:
```bash
composer install
cp .env.example .env    # then set: APP_KEY, DB_* (elibrarydb), MEILISEARCH_*, HERMES_GATEWAY_TOKEN
php artisan key:generate
php artisan migrate:fresh --seed        # schema-only elib_new.sql + 20 constant tables (SEED, don't plain-migrate a live DB)
# storage: uploads go DIRECTLY under public/ (the Windows storage:link symlinks are broken here)
php artisan scout:flush ; php artisan scout:import "App\Models\ViewCatalog"   # Meilisearch index
npm install && npm run build            # front-end assets
```
Details: **`smart-elibrary/LARAVEL_UPGRADE_RUNBOOK.md`** (framework/package specifics),
**`smart-elibrary/MEILISEARCH_SETUP.md`** (OPAC search), **`smart-elibrary/RAG_INTEGRATION_PLAN.md`**
(how the app is built RAG-ready: `catalog_id` join key, `public/` storage, `buildCitations()`).

> `HERMES_GATEWAY_TOKEN` in the eLibrary `.env` **must match** what `start-rag-api.ps1` uses — it's
> the shared secret between the two systems.

## 7. Wire the catalog sync  → `CATALOG_SYNC_RUNBOOK.md`

Connect live catalog changes to the RAG index:
```bash
cd rag
.venv/Scripts/python.exe lance_add_catalog_id.py     # one-time: add catalog_id column to LanceDB
.venv/Scripts/python.exe rag_sync_worker.py --reconcile   # initial backfill: enqueue all published
.venv/Scripts/python.exe rag_sync_worker.py --once --cpu  # drain -> index
```
Full operations (flags, nightly reconcile, scheduling, GPU contention): **`CATALOG_SYNC_RUNBOOK.md`**.

Day to day you don't run the worker by hand — either click **Run sync now** on the eLibrary
**AI Sync Monitor** (it calls the gateway's `POST /sync/drain`), or let the sync-worker task drain
the queue every 30 s.

**Make it survive reboots** — run once, **elevated**:
```powershell
powershell -ExecutionPolicy Bypass -File "<ProjectY>\register-projecty-tasks.ps1"
```
Registers logon tasks for llama-server (+15s), the RAG API (+60s) and the sync worker (+90s).
Without this **nothing auto-starts**, and AI mode is down after every restart until you hand-start
it (see `STARTING_HERMES_GUIDE.md`).

## 8. Verify end-to-end

1. `rag/rag_sync_worker.py --stats` → pending trends to 0.
2. eLibrary published record **View** page → **AI-index badge** reads *AI-indexed*.
3. Ask Hermes (CLI + Discord) a question answerable only from a catalog record → grounded, **cited**
   answer (via `search_docs`). Confirm `locked-rag` refuses off-corpus / extraction prompts.

---

## Documentation index (both repos)

### `hermes-with-rag` (this repo)
| Doc | Covers |
|---|---|
| **`DEPLOYMENT_RUNBOOK.md`** | this file — the ordered rebuild |
| `README.md` | architecture, how to start, model flags, GPU, troubleshooting |
| `STARTING_HERMES_GUIDE.md` | manual start/stop/test, post-reboot, profile switching |
| `SERVER_SPEC.md` | future dedicated inference server (70B, vLLM, parts list) |
| `PACKAGING_AND_DEPLOYMENT.md` | packaging plan (Docker Compose, code↔data split) |
| `HERMES_SECURITY_LOCKDOWN_2026-07-09.md` | the `locked-rag` hardening |
| `CATALOG_SYNC_RUNBOOK.md` | run/schedule the eLibrary→RAG sync worker (full reference) |
| `MANUAL_REINDEX_PROCEDURE.md` | short task guide: clear "Pending" rows in the AI Sync Monitor |
| `register-projecty-tasks.ps1` | register logon tasks (llama-server / rag-api / sync-worker) — run elevated |
| `start-sync-worker.ps1` | launcher: sync worker `--loop --interval 30 --cpu` |
| `CHANGES_2026-07-08_hermes-rag-toolcalling.md` | tool-calling fix log |
| `rag/README.md` | RAG pipeline: add papers, (re)build index, MCP tool |
| `rag/RAG_PLAN_AND_PROGRESS.md` | full RAG plan + progress (triage→OCR→chunk→embed) |
| `rag/RAG_TEST_QUESTIONS.md` | eval question set |
| `rag/requirements.txt` | pinned RAG venv deps |

### `smart-elibrary`
| Doc | Covers |
|---|---|
| `LARAVEL_UPGRADE_RUNBOOK.md` | Laravel 8→13 upgrade specifics |
| `MEILISEARCH_SETUP.md` | OPAC search (Scout/Meilisearch) |
| `RAG_INTEGRATION_PLAN.md` | how the app is built RAG-ready + the Hermes "Ask the Library" contract |
| `RAG_CATALOG_SYNC_PLAN.md` | catalog↔RAG live-sync design + what's built |
| `OPAC_SEO_PLAN.md` | public-launch SEO: per-page metadata, JSON-LD, sitemap/robots, URL slugs |
| `DEPLOYMENT_CHECKLIST.md` | **per-deploy** steps for the eLibrary + required `.env`, out-of-git state, cross-repo change record |
| `Z3950_SRU_INTEGRATION_PLAN.md` | (future) copy-cataloging via Z39.50/SRU |

---

## Reinstall order at a glance
```
0 prereqs (WSL2, uv, CUDA, Laragon/MySQL, Docker)
1 Hermes agent (WSL2)                 -> STARTING_HERMES_GUIDE.md
2 llama.cpp + model                   -> README.md / start-llama-server.ps1
3 gateway + RAG API + proxy           -> start-*.ps1
4 security lockdown (locked-rag)      -> HERMES_SECURITY_LOCKDOWN_2026-07-09.md
5 RAG corpus -> LanceDB               -> rag/README.md (+ rag/requirements.txt)
6 eLibrary (Laravel)                  -> smart-elibrary/ runbooks
7 catalog sync worker + migration     -> CATALOG_SYNC_RUNBOOK.md
8 verify end-to-end
```
