# Change record — Hermes RAG tool-calling fix (2026-07-08)

Consolidated record of every change made to fix "Hermes won't reliably use `search_docs`."
Full diagnosis lives in `rag/RAG_PLAN_AND_PROGRESS.md` (Stage 5). This file is the rollback reference.

## Why (root cause, proven — not skill/memory wording)
1. **Trap tools** — for "search my research" both Qwen variants grab `search_files` / `mcp_filesystem_search_files` over the real `mcp_docs_search_docs`.
2. **Thinking model derails** — Qwen3-30B-A3B-**Thinking** loops `search_files` 145× to the turn budget (~113 s/call). Instruct picks in ~1.5 s.
3. **Cold-start race** — docs MCP loaded BGE-M3 (~12 s) *before* answering the MCP handshake, so a fresh `hermes chat` finalized its toolset *without* the corpus tool.

Context: config lists **Opus as primary but it's unfunded** → every query falls back through OpenAI (also unfunded) to **local Qwen**. So the loaded local model is what actually runs.

## What changed

| # | File / setting | Before | After |
|---|---|---|---|
| 1 | `start-llama-server.ps1` — `$model` | `Qwen3-30B-A3B-Thinking-2507-Q4_K_M.gguf` | `Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf` |
| 2 | `start-llama-server.ps1` — `$argList` | (no temp flag) | added `'--temp','0'` (greedy; Hermes sends no temperature) |
| 3 | `~/AppData/Local/hermes/config.yaml` — `mcp_servers.filesystem.enabled` | `true` | `false` (removes `mcp_filesystem_*`, incl. the search trap) |
| 4 | Hermes toolsets (CLI + Discord) | `file`, `terminal` enabled | **disabled** via `hermes tools disable file terminal --platform cli` / `--platform discord` |
| 5 | `rag/rag_mcp_server.py` | BGE-M3 + reranker loaded at module top (blocks MCP handshake ~12 s) | background thread + `_models_ready` Event → tools register ~2.4 s; first search waits once. lancedb still opened main-thread (deadlock constraint). |

## Commands run (in order)
```powershell
# model swap + greedy sampling
taskkill /IM llama-server.exe /F
.\start-llama-server.ps1                       # now Instruct + --temp 0

# lean toolset
hermes tools disable file terminal --platform cli
hermes tools disable file terminal --platform discord
# (config.yaml filesystem.enabled -> false edited by hand)

# verify docs MCP now registers fast
hermes mcp test docs                           # Connected ~2.4s, 2 tools

# apply to Discord
hermes gateway stop
.\start-hermes-gateway.ps1                      # new gateway PID, warm docs
```

## Validation
`hermes chat -q "Search my research: at what age do rubber trees start latex tapping?" -s local-research-rag`
→ called `mcp_docs_search_docs`, retrieved real passages (Duke 1989; `FB-00963_…H009033` p.55),
answered **"5–8 years old / 45–50 cm trunk diameter"** grounded + cited, ended naturally
(`tool_turns=2`, `finish_reason=stop`, no loop).

## Tradeoffs / how to revert
- **Instruct** gives up the Thinking variant's deep multi-step reasoning. Revert: uncomment the Thinking `$model` line in `start-llama-server.ps1` (and optionally drop `--temp 0`), restart.
- **Lean toolset** means Hermes has no local filesystem/shell tools (by design — Claude Code covers that). Revert: `hermes tools enable file terminal --platform cli` (and `discord`); set `filesystem.enabled: true`; restart gateway.

## Standing caveats (unchanged by this work)
- Config primary = **Opus (anthropic), unfunded** → falls back to local Qwen. To actually use Opus, add Anthropic credits.
- `config.yaml` `fallback_providers` holds a **plaintext, live-looking OpenAI `sk-proj-…` key** — rotate if real.
