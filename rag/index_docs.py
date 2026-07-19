"""
Project Y — document RAG indexer.
Walks a folder of research/papers (.pdf, .txt, .md), extracts text, chunks it,
embeds with ChromaDB's bundled CPU model, and stores in a local Chroma DB.
Re-run any time you add/change files (idempotent: stable chunk IDs => upsert).

Folder: defaults to ./docs, override with env var RAG_DOCS_DIR.
"""
import os
import hashlib
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions
from pypdf import PdfReader

BASE = Path(__file__).resolve().parent
DOCS_DIR = Path(os.environ.get("RAG_DOCS_DIR", str(BASE / "docs")))
CHROMA_DIR = str(BASE / "chroma")
COLLECTION = "research"
EXTS = {".pdf", ".txt", ".md"}


def extract_text(path: Path) -> str:
    suf = path.suffix.lower()
    if suf in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="ignore")
    if suf == ".pdf":
        try:
            reader = PdfReader(str(path))
            return "\n".join((pg.extract_text() or "") for pg in reader.pages)
        except Exception as e:
            print(f"  ! PDF read failed for {path.name}: {e}")
            return ""
    return ""


def chunk(text: str, size: int = 1000, overlap: int = 150):
    words = text.split()
    out, i = [], 0
    while i < len(words):
        piece = " ".join(words[i:i + size]).strip()
        if piece:
            out.append(piece)
        i += size - overlap
    return out


def main():
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    ef = embedding_functions.DefaultEmbeddingFunction()  # all-MiniLM-L6-v2, ONNX, CPU
    col = client.get_or_create_collection(name=COLLECTION, embedding_function=ef)

    files = [p for p in DOCS_DIR.rglob("*") if p.suffix.lower() in EXTS]
    print(f"Docs folder : {DOCS_DIR}")
    print(f"Found       : {len(files)} file(s)")
    if not files:
        print("No .pdf/.txt/.md files found. Drop your papers in the folder above and re-run.")
        return

    total = 0
    for f in files:
        text = extract_text(f)
        if not text.strip():
            print(f"  - {f.name}: no extractable text (scanned PDF?) — skipped")
            continue
        chunks = chunk(text)
        ids = [hashlib.md5(f"{f}::{i}".encode()).hexdigest() for i in range(len(chunks))]
        metas = [{"source": f.name, "path": str(f), "chunk": i} for i in range(len(chunks))]
        B = 100
        for j in range(0, len(ids), B):
            col.upsert(ids=ids[j:j + B], documents=chunks[j:j + B], metadatas=metas[j:j + B])
        total += len(chunks)
        print(f"  + {f.name}: {len(chunks)} chunks")

    print(f"\nDone. Indexed {total} chunks this run. Collection now holds {col.count()} chunks.")


if __name__ == "__main__":
    main()
