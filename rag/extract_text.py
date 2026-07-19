"""
Stage 2.1 — Extract text from the TEXT_READY bucket (no OCR).

Reads `triage_output/triage_results.csv`, takes every file classified
TEXT_READY, and pulls its existing digital text layer with PyMuPDF (fitz) —
page by page, no OCR. Writes one JSON per document to `stage2/extracted/`:

    { "source": "...pdf", "stem": "...", "pages_total": N,
      "pages": [ {"page": 1, "text": "..."}, ... ] }

Resumable: a document whose output JSON already exists is skipped. Run it in
your own terminal to see the progress modal:

    cd C:\\Users\\jcvia\\PyCharmMiscProject\\ProjectY\\rag
    .\\.venv\\Scripts\\python.exe .\\extract_text.py

This is CPU-only and safe to run while llama-server is up (no GPU use).
"""
from __future__ import annotations
import csv
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import fitz  # PyMuPDF

try:
    from progress_modal import ProgressModal
except Exception:
    ProgressModal = None

HERE = Path(__file__).resolve().parent
TRIAGE_CSV = HERE / "triage_output" / "triage_results.csv"
OUT_DIR = HERE / "stage2" / "extracted"

# Drop pages with essentially no extractable text (defensive: a TEXT_READY doc
# may still have an occasional image-only page).
MIN_PAGE_CHARS = 1


def main() -> int:
    if not TRIAGE_CSV.exists():
        print(f"ERR: no triage results at {TRIAGE_CSV}. Run triage first.", file=sys.stderr)
        return 2
    with TRIAGE_CSV.open("r", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["bucket"] == "TEXT_READY"]
    if not rows:
        print("No TEXT_READY files in triage results.", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Extracting text from {len(rows)} TEXT_READY files -> {OUT_DIR}/\n")
    pm = ProgressModal("Stage 2: text extraction (no OCR)", len(rows),
                       "PyMuPDF text layer") if ProgressModal else None

    done = skipped = errors = 0
    total_pages = total_chars = 0
    for i, r in enumerate(rows, 1):
        src = Path(r["path"])
        out = OUT_DIR / f"{src.stem}.json"
        if pm:
            pm.update(i - 1, src.name)
            if pm.cancelled:
                print("\nCancelled via progress window.")
                break
        if out.exists():
            skipped += 1
            continue
        try:
            doc = fitz.open(HERE / src)
            pages = []
            for pno in range(doc.page_count):
                text = doc.load_page(pno).get_text("text").strip()
                if len(text) >= MIN_PAGE_CHARS:
                    pages.append({"page": pno + 1, "text": text})
                    total_chars += len(text)
            doc.close()
            rec = {"source": str(src), "stem": src.stem,
                   "pages_total": len(pages), "pages": pages}
            out.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            done += 1
            total_pages += len(pages)
        except Exception as e:
            errors += 1
            print(f"  ERROR {src.name}: {type(e).__name__}: {e}")

    if pm:
        pm.close("Extraction complete")
    print("\n" + "=" * 70)
    print("TEXT EXTRACTION DONE")
    print("=" * 70)
    print(f"  extracted: {done}   skipped(existing): {skipped}   errors: {errors}")
    print(f"  pages with text: {total_pages:,}   chars: {total_chars:,}")
    print(f"  output: {OUT_DIR}/")
    print(f"  next: chunk.py  ->  embed_index.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
