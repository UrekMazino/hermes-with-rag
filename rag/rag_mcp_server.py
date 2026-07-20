"""
Project Y — document RAG MCP server (LanceDB + BGE-M3 backend).

Exposes `search_docs` and `docs_status` to Hermes over stdio MCP, backed by the
LanceDB table embed_index.py builds (`rag/lancedb`, table `research`).

CRITICAL — why the heavy init is at module top (DO NOT move it into the tools):
LanceDB's native (Rust) extension DEADLOCKS if it is first imported/initialized
in a worker thread while an asyncio event loop is running. FastMCP runs tool
calls inside its event loop (and offloads sync work to worker threads), so any
lazy `import lancedb` / connect / open_table inside a tool hangs the call until
the client times out (this was the 120s `docs_status` timeout). Fix: import +
connect + open the table, and load BGE-M3, HERE in the main thread before
mcp.run(). The async tools then only *use* those objects, running the sync
LanceDB/BGE calls via asyncio.to_thread (safe once imported in the main thread).
"""
import asyncio
import os
import sys
import time
from pathlib import Path

# Windows w/o Developer Mode can't make HF cache symlinks (WinError 1314) -> copy.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
# HF fast tokenizers can deadlock across threads -> disable their parallelism.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

BASE = Path(__file__).resolve().parent
LANCE_DIR = str(BASE / "lancedb")
TABLE = "research"


def _log(msg: str) -> None:
    print(f"[rag_mcp_server] {msg}", file=sys.stderr, flush=True)


# --- MAIN-THREAD initialization (must happen before the event loop starts) ---
import lancedb

_tbl = None
try:
    _db = lancedb.connect(LANCE_DIR)
    if TABLE in _db.table_names():
        _tbl = _db.open_table(TABLE)
        _log(f"opened LanceDB table '{TABLE}' ({_tbl.count_rows():,} rows)")
    else:
        _log(f"LanceDB table '{TABLE}' not found at {LANCE_DIR}")
except Exception as e:  # index not built yet, etc.
    _log(f"LanceDB open failed: {type(e).__name__}: {e}")
    _tbl = None

import threading

# Load BGE-M3 + reranker HERE, at module top, in the MAIN thread — BEFORE FastMCP's
# asyncio loop starts (mcp.run()). This blocks the MCP handshake ~12s (registration is
# slow), but it is the only RELIABLE option: loading these torch/FlagEmbedding models in
# a BACKGROUND thread while the event loop is running intermittently HANGS the reranker
# init (process stalls at ~734 MB, _models_ready never sets, every search times out) —
# the same thread-vs-asyncio hazard that forces lancedb to load here too. Reliability of
# search beats fast registration; the warm gateway pays this ~12s once at startup.
_model = None
try:
    from FlagEmbedding import BGEM3FlagModel
    _model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False, device="cpu")
    _log("BGE-M3 model loaded (CPU)")
except Exception as e:
    _log(f"BGE-M3 load failed: {type(e).__name__}: {e}")
    _model = None

_reranker = None
try:
    from FlagEmbedding import FlagReranker
    _reranker = FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=False)  # CPU at query time
    _log("bge-reranker-v2-m3 loaded (CPU)")
except Exception as e:
    _log(f"reranker load failed: {type(e).__name__}: {e}")
    _reranker = None

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("docs-rag")

# Stage 4 hybrid retrieval tunables: dense (exact vector) + sparse (FTS/BM25),
# fused by Reciprocal Rank Fusion, then reordered by the cross-encoder reranker.
RETRIEVE_N = 30   # candidates from each retriever
RERANK_N = 24     # max fused candidates to rerank (was 40; trimmed to cut CPU-rerank latency
                  # so the first cold-session search stays under Hermes' MCP call timeout)
RRF_K = 60

# HyDE (Hypothetical Document Embeddings): generate a hypothetical answer via the
# local LLM, embed query+hypothetical for the DENSE side -> recovers vocabulary-
# mismatch queries (e.g. "profitability" -> ROI/FCR/net-margin). Opt-in (slow on
# the local thinking model, ~20s); FTS + rerank still use the RAW query.
LLAMA_URL = "http://127.0.0.1:8080/v1/chat/completions"
LLAMA_MODEL = "qwen3-30b"
HYDE_MAX_TOKENS = 1024
HYDE_TIMEOUT = 60


def _hyde_sync(query: str) -> str:
    """Generate a hypothetical answer passage; '' on any failure (graceful fallback)."""
    import json
    import urllib.request
    try:
        body = json.dumps({
            "model": LLAMA_MODEL,
            "messages": [{"role": "user", "content":
                "Write a short factual passage (4-6 sentences) that would answer this question, "
                "as if from a technical report. Question: " + query}],
            "max_tokens": HYDE_MAX_TOKENS, "temperature": 0.0,
        }).encode()
        req = urllib.request.Request(LLAMA_URL, data=body, headers={
            "Content-Type": "application/json", "Authorization": "Bearer sk-local"})
        r = json.loads(urllib.request.urlopen(req, timeout=HYDE_TIMEOUT).read())
        return (r["choices"][0]["message"].get("content") or "").strip()
    except Exception as e:
        _log(f"HyDE failed ({type(e).__name__}: {e}); falling back to plain query")
        return ""


def _rrf(dense_rows, fts_rows):
    scores, rowmap = {}, {}
    for rank, r in enumerate(dense_rows):
        cid = r["chunk_id"]; rowmap[cid] = r
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, r in enumerate(fts_rows):
        cid = r["chunk_id"]; rowmap.setdefault(cid, r)
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return [rowmap[c] for c in sorted(scores, key=lambda c: -scores[c])]


# Serialize searches. BGE-M3 and the reranker are single shared instances and lancedb
# table handles aren't safe to drive from two calls at once. Under the cold-start retry
# pattern (a first call still running server-side when the client times out and the agent
# fires a second) this raised a Rust "already borrowed" panic. One lock => calls queue.
_search_lock = threading.Lock()

# A LanceDB table handle is a snapshot of ONE dataset version. A long-running server (rag_api, or
# the docs MCP under the gateway) therefore never sees rows written by ANOTHER process — e.g. the
# catalog-sync worker — and newly synced records stay invisible until the server restarts.
# checkout_latest() is a cheap metadata refresh; TTL'd so back-to-back queries don't re-check.
_TBL_REFRESH_SEC = 15.0
_tbl_checked_at = 0.0


def _refresh_tbl() -> None:
    """Pick up rows added by another process. Never raises — on failure we keep the old handle."""
    global _tbl_checked_at
    if _tbl is None:
        return
    now = time.monotonic()
    if now - _tbl_checked_at < _TBL_REFRESH_SEC:
        return
    _tbl_checked_at = now
    try:
        _tbl.checkout_latest()
    except Exception as e:
        _log(f"table refresh skipped: {type(e).__name__}: {e}")


def _search_sync(query: str, k: int, hyde: bool = False):
    """Hybrid (dense+FTS, RRF) retrieve -> cross-encoder rerank -> top k.
    Returns list of (row, score); score is the reranker prob (dense sim on fallback).
    hyde=True: embed query+hypothetical-answer for the DENSE side (FTS + rerank use raw query)."""
    with _search_lock:
        _refresh_tbl()   # so freshly-synced catalog records are visible without a restart
        dense_text = query
        if hyde:
            h = _hyde_sync(query)
            if h:
                dense_text = query + "\n" + h
        qvec = _model.encode([dense_text], max_length=512)["dense_vecs"][0]
        dense = _tbl.search(qvec.tolist()).metric("cosine").limit(RETRIEVE_N).to_list()
        try:
            fts = _tbl.search(query, query_type="fts").limit(RETRIEVE_N).to_list()
        except Exception:
            fts = []
        cands = _rrf(dense, fts)[:RERANK_N] if fts else dense[:RERANK_N]
        if _reranker is not None and cands:
            # In HyDE mode rerank against the enriched text (query+hypothetical) — it carries the
            # domain vocabulary that matches table/numeric target chunks the raw query misses.
            rerank_query = dense_text
            scores = _reranker.compute_score([[rerank_query, c.get("text") or ""] for c in cands], normalize=True)
            if not isinstance(scores, list):
                scores = [scores]
            return sorted(zip(cands, scores), key=lambda x: -x[1])[:k]
        # fallback: dense similarity only
        out = []
        for r in dense[:k]:
            dist = r.get("_distance")
            out.append((r, (1.0 - float(dist)) if dist is not None else 0.0))
        return out


@mcp.tool()
async def search_docs(query: str, k: int = 5, hyde: bool = False) -> str:
    """Semantic search over the user's local research/papers corpus. Returns the
    most relevant passages with their source filename, page, and a relevance
    score. Use this to ground answers in the user's own documents instead of
    relying on memory or guessing. `k` = number of passages (1-10).
    Set `hyde=True` to RETRY a query that returned weak/no results: it generates a
    hypothetical answer first to bridge vocabulary gaps (e.g. "profitability" ->
    ROI/margins). Slower (~20s on the local model) — use only as a fallback, not by default."""
    if _tbl is None:
        return ("The document index is empty/not built yet. Run extract_text.py -> "
                "chunk.py -> embed_index.py to build the LanceDB index.")
    if _model is None:
        return "Embedding model unavailable on the server; cannot search."
    k = max(1, min(int(k), 10))
    results = await asyncio.to_thread(_search_sync, query, k, bool(hyde))
    if not results:
        return f"No relevant passages found for: {query}"
    lines = [f"Top {len(results)} passage(s) for: {query}\n"]
    for i, (r, score) in enumerate(results, 1):
        src = r.get("stem") or r.get("source", "?")
        page = r.get("page", "?")
        snippet = " ".join((r.get("text") or "").split())
        lines.append(f"[{i}] {src} (p.{page}, score {float(score):.2f})\n{snippet}\n")
    return "\n".join(lines)


@mcp.tool()
async def docs_status() -> str:
    """Report how many document chunks are currently indexed in the local corpus."""
    if _tbl is None:
        return "Document index not built yet (no LanceDB table 'research')."
    n = await asyncio.to_thread(_tbl.count_rows)
    return f"Indexed chunks: {n:,} (LanceDB table '{TABLE}')."


if __name__ == "__main__":
    mcp.run()  # stdio transport (what Hermes' MCP client expects)
