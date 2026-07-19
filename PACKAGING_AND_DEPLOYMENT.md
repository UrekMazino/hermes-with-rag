# Project Y — Packaging & Deployment Spec

**Purpose:** how to package the Hermes / Agent / RAG stack so it (a) installs cleanly on **this
Windows PoC box** now and (b) deploys to a **real server later** (hardware in `SERVER_SPEC.md`) with
minimal rework. This is a **spec / decisions doc — no code yet.**

Complements: `SERVER_SPEC.md` (the future server's *hardware/capacity*), `STARTING_HERMES_GUIDE.md`
(how it's started *today*, by hand/scheduled tasks), and the eLibrary `RAG_INTEGRATION_PLAN.md` §10
(the *consumer* — the Laravel chat that will call the gateway).

---

## 1. What we're actually packaging (components today)

| Component | Today | GPU? |
|---|---|---|
| **llama-server** | `llama-cpp\llama-server.exe` + `models\Qwen3-30B-A3B-Instruct-…Q4_K_M.gguf`, `127.0.0.1:8080`, MoE offload (`--n-cpu-moe 26`), `-c 65536`, `--parallel 1` (`start-llama-server.ps1`) | **GPU + CPU/RAM** (MoE) |
| **Hermes agent + gateway** | installed *outside the repo* at `%LOCALAPPDATA%\hermes\hermes-agent` (own venv); `hermes gateway run` (`start-hermes-gateway.ps1`). Owns the agent loop + `locked-rag` profile | no (calls llama-server) |
| **RAG docs service** | `rag/rag_mcp_server.py` + rag Python venv; **LanceDB** at `rag/lancedb`, BGE-M3 + bge-reranker-v2-m3; exposes `search_docs` | **query-time = CPU**; index-time = GPU |
| **nginx proxy** | winget nginx, prefix `C:\Users\jcvia\nginx-proxy`, basic-auth on `:80` (`start-nginx-proxy.ps1`) | no |
| **Indexing / OCR batch** | `start-ocr-batch.ps1`, `rag/ocr_run.py` (Marker/Surya), triage/chunk/embed | **GPU-heavy, offline** |
| **Data** | `models/*.gguf`, `llama-cpp/`, `rag/lancedb` (index), `rag/pdf` + `rag/ocr_out` (corpus) | — |

Started today as ~5 loose PowerShell scripts / scheduled tasks. That's the thing to consolidate.

---

## 2. The core reframe: SERVING vs INDEXING → two artifacts

- **Serving runtime** — the always-on, *installable/deployable* product: **llama-server + Hermes
  gateway + RAG query service + nginx**. Query-time retrieval (embed+rerank a handful of candidates)
  runs on **CPU**; only llama-server needs the GPU. Modest, always-on.
- **Indexing pipeline** — an *offline, occasional, GPU-heavy* tool: triage → OCR (Marker) → chunk →
  BGE-M3 embed → LanceDB build. Run it to **(re)build the index**; it is **not** part of the always-on
  install. It produces the index the serving runtime mounts.

**Why:** this keeps the deployed artifact small (inference + CPU retrieval + a *pre-built* index) and
keeps the ML-training-weight deps (torch+CUDA+marker/surya) out of the shipped product.

---

## 3. Code ↔ data separation (prerequisite for any packaging)

| Ship (small, versioned) | Fetch / mount (big, external) |
|---|---|
| Python code (`rag/`, gateway), config, requirements **lock**, nginx conf, `locked-rag` profile | Model weights (`Qwen3-30B` GGUF ~15–20 GB; future Llama-3.3-70B), BGE-M3 + reranker weights, **LanceDB index** + OCR outputs |

Config points at data **paths/URLs**; nothing hardcoded. First run either **downloads** weights
(Hugging Face) or **mounts** a prepared data volume. Never bake multi-GB weights/index into the
installer or image.

---

## 4. One config surface + one control (replaces the 5 scripts)

- **Single config** (`projecty.yaml` / `.env`): model paths, ports (llama `8080`, gateway, RAG,
  nginx `80`), GPU layers / `--n-cpu-moe`, active profile (`locked-rag`), tokens
  (`HERMES_GATEWAY_TOKEN`, nginx basic-auth), data dirs.
- **One control** to **start / stop / health-check all components together**, registered so it's
  **always-on + auto-restart** (Windows service / `docker compose` / NSSM). This supersedes the
  manual `start-*.ps1` flow in `STARTING_HERMES_GUIDE.md`.

---

## 5. Recommended packaging: **Docker Compose** (Windows now → server later, one artifact)

The single best answer to "easier deployment": **write once, run both places.**
- **Compose services:** `llama` (GPU generation), `rag` (CPU retrieval + Hermes gateway), `nginx`
  (edge/basic-auth). Weights + index as **named volumes / bind mounts** — not baked in. One
  `docker compose up`.
- **Windows now:** Docker Desktop + **WSL2 + NVIDIA CUDA-on-WSL** for the `llama` GPU service.
  **Server later:** the *same* compose file (Linux native GPU).
- **Recommended starting variant — the hybrid:** keep **llama-server native on the host GPU** (bare
  metal, simplest, matches today) and containerize only the **CPU-side** `rag`/gateway/`nginx`, which
  reach llama-server over `host.docker.internal`/localhost. This **sidesteps container-GPU-passthrough
  friction** for the serving path. Move llama into a GPU container later for full one-command parity.
- **Hermes packaging:** it lives at `%LOCALAPPDATA%\hermes` today (outside the repo). To containerize,
  **pin it as a version-locked pip dependency** in the `rag`/gateway image so the image is
  self-contained (no external install step).

---

## 6. Alternatives (and why not, given the server goal)

- **Native Windows services (NSSM)** wrapping the PS scripts — simplest for *this* box, a fine PoC
  stopgap, but **does not move to a Linux server** → re-packaging. Use only if Docker is deferred.
- **Inno Setup / MSI installer** — gives the "install this software" feel; wrap the bootstrap.
  Windows-only, same server-portability gap. Good for a *demo build* of the PoC.
- **PyInstaller / Nuitka single .exe** — **avoid.** A torch + CUDA + lancedb + marker stack freezes
  poorly: brittle, enormous, hard to debug. Not worth it.

---

## 7. Honest constraints (set expectations)

- **NVIDIA GPU + driver is a hard requirement** (llama-server). This is not a GPU-free, runs-anywhere
  installer.
- **Weights + index are big** → download-on-first-run or mount a volume; don't ship them inside the
  artifact.
- **Windows GPU-in-Docker** needs WSL2 + CUDA-on-WSL — hence the §5 hybrid (host llama-server) to
  start.
- The current **30B is MoE with CPU offload** (`--n-cpu-moe 26`) → it uses GPU **and** CPU/RAM; the
  future **70B** (`SERVER_SPEC.md`) is GPU-resident on the big card. Config must expose these knobs.

---

## 8. How it relates to the other docs

- **`SERVER_SPEC.md`** = the *hardware/capacity* of the future server (Llama-3.3-70B, ~100 users/day,
  memory-bound). **This doc** = the *software packaging* to deploy onto it. The serving-runtime image
  is the payload; SERVER_SPEC is the box it lands on.
- **`STARTING_HERMES_GUIDE.md`** = today's manual start → **superseded** by the §4 single control once
  packaged.
- **eLibrary `RAG_INTEGRATION_PLAN.md` §10** = the *consumer*. The Hermes gateway it targets is a
  service in this package; the eLibrary only needs the **gateway URL + `HERMES_GATEWAY_TOKEN` as
  config** (localhost now, a container/server address later). No eLibrary change when the AI stack
  moves boxes.

---

## 9. Suggested build order (packaging — do later, no code yet)

1. **Config + code/data split** — extract all paths/ports/tokens to one config; move weights/index to
   a data dir. *Highest value; unblocks everything and is what makes "server later" trivial.*
2. **Pin dependencies** — a requirements **lock** for the rag venv + pin the Hermes version.
3. **One orchestrating control** — compose *or* a single script that starts/stops/health-checks all
   components; register it as a service (auto-restart).
4. **Serving Docker image** — `rag` + gateway + `nginx` (CPU), plus a compose that uses the **host**
   llama-server (the §5 hybrid). Verify parity with the manual setup.
5. **Optional** — full GPU-container llama for one-command parity; and/or an Inno Setup installer for
   demo builds.
6. Keep **indexing** as its own documented offline job (already scripted) — never in the serving
   artifact.

---

## 10. Open decisions

- **Weights:** bundle vs first-run download (recommend **download/mount**; document a "prepare data"
  step).
- **Hermes:** in-image pip-pinned vs external install (recommend **in-image** for self-containment).
- **GPU strategy:** host llama-server + containerized rest (hybrid) vs full-compose GPU (recommend
  **hybrid now, full later**).
- **Secrets:** `.env` for the PoC vs a secrets store on the server.
- **Model swap on the server:** the config must let the same artifact run Qwen-30B (dev box) or
  Llama-70B (server) by pointing at a different GGUF — verify the gateway/agent are model-agnostic.

> This file is the **packaging/deployment** view. Retrieval-quality and RAG-pipeline specifics live in
> `rag/RAG_PLAN_AND_PROGRESS.md`; security in `HERMES_SECURITY_LOCKDOWN_2026-07-09.md`; the future
> server hardware in `SERVER_SPEC.md`.
