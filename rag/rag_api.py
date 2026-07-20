"""Local HTTP gateway for eLibrary "AI mode".

A thin FastAPI service the Laravel app calls server-side to get a grounded, cited
answer from the local corpus. It REUSES the exact retrieval stack the Hermes agent
uses (rag_mcp_server._search_sync: hybrid dense+FTS, RRF, cross-encoder rerank), then
synthesises an answer with the local Qwen (llama-server) strictly from the retrieved
passages — abstaining when they don't support an answer.

Contract (RAG_INTEGRATION_PLAN.md §10.2), returned by POST /agent/answer:
    {answer, sources:[{stem, page, score, snippet}], abstained, took_ms}
Laravel maps each source's `stem` -> catalog_id (view_catalogs.accession_number) for
the citation bridge, so nothing here needs to know about catalog ids.

Exposure (§10.3): binds 127.0.0.1 only; a shared bearer token (HERMES_GATEWAY_TOKEN)
gates every non-health route. The browser never talks to this — only Laravel does.

Run:  rag/.venv/Scripts/python.exe -m uvicorn rag_api:app --host 127.0.0.1 --port 8090
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Importing the MCP server module loads BGE-M3 + reranker + opens LanceDB at import time
# (its mcp.run() is guarded by __main__, so nothing is served here). This is the same
# code path the Hermes agent's search_docs uses — one retrieval implementation, not two.
import rag_mcp_server as rag

LLAMA_URL = "http://127.0.0.1:8080/v1/chat/completions"
LLAMA_MODEL = "qwen3-30b"
GATEWAY_TOKEN = os.environ.get("HERMES_GATEWAY_TOKEN", "")

# How many passages to retrieve/rerank and hand to the model as citable sources.
ANSWER_K = 6
SNIPPET_CHARS = 320
SYNTH_MAX_TOKENS = 700
SYNTH_TEMPERATURE = 0.3
SYNTH_TIMEOUT = 120

# /sync/drain: how many queued catalog-sync items one call applies. Bounded on purpose —
# embedding shares the single BGE-M3 instance with search, so an unbounded drain would stall
# AI-mode queries and time out the HTTP request. The caller repeats while remaining > 0.
DRAIN_LIMIT = 25
DRAIN_MAX = 200

# The model emits this sentinel when the passages can't answer -> we surface `abstained`.
ABSTAIN = "INSUFFICIENT_CONTEXT"

SYSTEM = (
    "You are the assistant for a research library's public catalogue. Answer the user's "
    "question using ONLY the numbered passages provided, which are excerpts from documents "
    "in the collection.\n"
    "Rules:\n"
    "- Use only what the passages say. Do NOT add outside knowledge or general facts.\n"
    "- Cite every claim inline with the passage number(s) it comes from, like [1] or [2][3].\n"
    "- Never invent a citation number that isn't in the passages.\n"
    f"- If the passages do not contain enough information to answer, reply with EXACTLY "
    f"this single token and nothing else: {ABSTAIN}\n"
    "- Be concise and factual: 2-5 sentences. Do not restate the question."
)

app = FastAPI(title="eLibrary RAG gateway", docs_url=None, redoc_url=None)


class AnswerRequest(BaseModel):
    query: str
    k: int | None = None
    hyde: bool | None = False


def _check_token(authorization: str | None) -> None:
    # If no token is configured the service refuses everything rather than run open.
    if not GATEWAY_TOKEN:
        raise HTTPException(status_code=503, detail="gateway token not configured")
    expected = f"Bearer {GATEWAY_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


def _synthesize(query: str, passages: list[dict]) -> str:
    """Call llama-server with the numbered passages; return the model's answer text."""
    numbered = "\n\n".join(
        f"[{i}] (source: {p['stem']}, p.{p['page']})\n{p['text']}"
        for i, p in enumerate(passages, 1)
    )
    body = json.dumps({
        "model": LLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"PASSAGES:\n{numbered}\n\nQUESTION: {query}\n\nANSWER:"},
        ],
        "max_tokens": SYNTH_MAX_TOKENS,
        "temperature": SYNTH_TEMPERATURE,
    }).encode()
    req = urllib.request.Request(
        LLAMA_URL, data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer sk-local"},
    )
    r = json.loads(urllib.request.urlopen(req, timeout=SYNTH_TIMEOUT).read())
    return (r["choices"][0]["message"].get("content") or "").strip()


@app.get("/healthz")
def healthz():
    """Liveness for Laravel's status card (§10.7). No auth, no model call."""
    ok = rag._tbl is not None and rag._model is not None
    docs = 0
    try:
        if rag._tbl is not None:
            docs = rag._tbl.count_rows()
    except Exception:
        pass
    return {
        "ok": ok,
        "index_ready": rag._tbl is not None,
        "embedder_ready": rag._model is not None,
        "reranker_ready": rag._reranker is not None,
        "docs": docs,
    }


@app.post("/agent/answer")
def answer(req: AnswerRequest, authorization: str | None = Header(default=None)):
    _check_token(authorization)

    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=422, detail="empty query")

    started = time.time()
    k = req.k or ANSWER_K

    # 1) Retrieve — the same hybrid+rerank path Hermes uses.
    try:
        hits = rag._search_sync(query, k, bool(req.hyde))
    except Exception as e:
        return JSONResponse(status_code=503, content={"error": f"retrieval failed: {type(e).__name__}"})

    passages = []
    for row, score in hits:
        text = " ".join((row.get("text") or "").split())
        if not text:
            continue
        passages.append({
            "stem": row.get("stem") or row.get("source") or "",
            "page": row.get("page"),
            "score": round(float(score), 3),
            "text": text,
        })

    # Nothing retrieved -> abstain (don't call the model at all).
    if not passages:
        return {
            "answer": "",
            "sources": [],
            "abstained": True,
            "took_ms": int((time.time() - started) * 1000),
        }

    # 2) Synthesise, strictly grounded in the passages.
    try:
        raw = _synthesize(query, passages)
    except Exception as e:
        return JSONResponse(status_code=503, content={"error": f"synthesis failed: {type(e).__name__}"})

    abstained = (raw == "") or (ABSTAIN in raw)

    sources = [] if abstained else [
        {"stem": p["stem"], "page": p["page"], "score": p["score"],
         "snippet": p["text"][:SNIPPET_CHARS]}
        for p in passages
    ]

    return {
        "answer": "" if abstained else raw,
        "sources": sources,
        "abstained": abstained,
        "took_ms": int((time.time() - started) * 1000),
    }


class DrainRequest(BaseModel):
    limit: int | None = None


@app.post("/sync/drain")
def sync_drain(req: DrainRequest | None = None, authorization: str | None = Header(default=None)):
    """
    Apply pending catalog-sync items (the eLibrary "Run sync now" button).

    Why here instead of Laravel shelling out to Python: this process already has BGE-M3 loaded
    and LanceDB open, so a drain costs milliseconds of setup instead of a 30-60 s model load,
    and it needs no extra credentials or exec() surface.

    Bounded on purpose — embedding shares the single BGE-M3 instance with search, so we take the
    same lock and cap the batch. The caller re-clicks (or polls) while `remaining > 0`.
    """
    _check_token(authorization)
    started = time.time()

    import rag_sync_worker as worker

    limit = max(1, min(int((req.limit if req else None) or DRAIN_LIMIT), DRAIN_MAX))

    # Reuse the model this process already holds instead of loading a second copy.
    worker.Embedder.use_model(rag._model)

    processed = failed = 0
    try:
        conn = worker.db_conn()
    except Exception as e:
        return JSONResponse(status_code=503,
                            content={"error": f"db unavailable: {type(e).__name__}: {e}"})
    try:
        items = worker.claim_batch(conn, limit)
        for item in items:
            # Serialize with search: BGE-M3 is one shared instance and isn't safe to drive twice.
            try:
                with rag._search_lock:
                    worker.process_item(conn, item, dry_run=False)
                worker.mark_done(conn, item["id"])
                processed += 1
            except Exception as e:
                worker.mark_error(conn, item["id"], f"{type(e).__name__}: {e}")
                failed += 1

        remaining = worker.queue_stats(conn)["pending"] or 0
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # Newly written rows live in a newer LanceDB version — drop the refresh TTL so the very next
    # search re-opens at the latest version and can actually see what we just indexed.
    rag._tbl_checked_at = 0.0

    return {
        "processed": processed,
        "failed": failed,
        "remaining": int(remaining),
        "took_ms": int((time.time() - started) * 1000),
    }
