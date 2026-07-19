# Project Y — Document RAG for Hermes

Local semantic search over your research/papers, exposed to Hermes as a
`search_docs` MCP tool. Everything runs on CPU (no VRAM used — the 4080 stays
free for Qwen).

## Architecture
```
rag/docs/  --(index_docs.py)-->  rag/chroma/ (vector store on disk)
                                       ^
Hermes  --(search_docs MCP tool)-->  rag_mcp_server.py  --/
```
- Embeddings: ChromaDB bundled `all-MiniLM-L6-v2` (ONNX, CPU). Model cached at `~/.cache/chroma`.
- Vector store: ChromaDB persistent in `rag/chroma/` (no server to run).
- Isolated venv: `rag/.venv` (chromadb, mcp, pypdf) — separate from Hermes.

## How to use

### 1. Add your papers
Drop `.pdf`, `.txt`, or `.md` files into:
```
C:\Users\jcvia\PyCharmMiscProject\ProjectY\rag\docs\
```
(Or point elsewhere: set env var `RAG_DOCS_DIR` to any folder before indexing —
e.g. your OneDrive\Documents\Papers.)

### 2. (Re)build the index — run after adding/changing files
```powershell
& "C:\Users\jcvia\PyCharmMiscProject\ProjectY\rag\.venv\Scripts\python.exe" `
  "C:\Users\jcvia\PyCharmMiscProject\ProjectY\rag\index_docs.py"
```
Idempotent (stable chunk IDs => re-running updates, doesn't duplicate).

### 3. Ask Hermes
Just ask a question whose answer is in your docs — Hermes calls `search_docs`
automatically, e.g. *"Search my papers for what they say about X and summarize
with citations."* (In Discord too, once the gateway has been restarted after any
MCP change.)

## Maintenance / notes
- **After adding papers**, re-run step 2. No restart needed for the CLI; for
  Discord restart the gateway so the running server sees the refreshed index
  is automatic (the server reads the same `chroma/` dir live) — re-index is enough.
- **Scanned/image PDFs** extract no text (you'll see "no extractable text").
  For those, OCR first (Hermes' `ocr-and-documents` skill / `marker-pdf`), or
  add `pymupdf` for better extraction.
- **Upgrade embeddings** (optional): swap `DefaultEmbeddingFunction()` for a
  stronger model (e.g. `bge-base-en-v1.5`) in both `index_docs.py` and
  `rag_mcp_server.py`, then re-index. Keep them identical in both files.
- The relevance "score" shown is a rough indicator; ranking order is what matters.

## Manage the MCP tool in Hermes
```powershell
$h = "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\hermes.exe"
& $h mcp list                 # see 'docs' server + filesystem
& $h mcp test docs            # connection test
& $h mcp remove docs          # unwire (re-add with: hermes mcp add docs --command <rag .venv python> --args <rag_mcp_server.py>)
```
