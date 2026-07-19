#!/usr/bin/env python3
"""
PDF Triage + OCR Sampling — for a large scanned-PDF corpus.

TWO PHASES, run separately:

  Phase 1 (triage):  classify EVERY pdf into one of three buckets WITHOUT OCR.
                     Fast, cheap, gives you the real text-ready vs needs-OCR split.
                       python pdf_triage_and_sample.py triage  /path/to/pdfs

  Phase 2 (sample):  OCR a REPRESENTATIVE SAMPLE of the needs-OCR bucket and dump
                     the text so you can READ it and judge OCR quality before
                     committing to OCR'ing thousands of files.
                       python pdf_triage_and_sample.py sample  --n 30

WHY TWO PHASES: OCR is the dominant cost on a scanned corpus AND its quality
caps your entire RAG. You do NOT want to OCR a file that already has good text,
and you do NOT want to discover bad OCR on document #4000. Triage tells you how
many actually need OCR; sampling lets you eyeball quality on a few dozen before
running the expensive full job.

DEPENDENCIES (install on your machine):
    pip install pymupdf            # 'fitz' — fast text-layer inspection (Phase 1)
    # For Phase 2, pick ONE OCR backend and install it (see OCR_BACKEND below):
    #   pip install pytesseract pillow   + system 'tesseract' binary   (CPU baseline)
    #   pip install paddleocr            (GPU-capable, better on your 4080S)
    #   pip install doctr                (GPU-capable, deep-learning OCR)

This script is read-only on your PDFs. It writes only to ./triage_output/.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERR: PyMuPDF not installed. Run: pip install pymupdf", file=sys.stderr)
    sys.exit(1)

# Make console output UTF-8 safe (Windows cp1252 console can't encode arrows/box chars).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Reusable GUI progress modal (lives next to this script; headless-safe no-op if no display).
try:
    from progress_modal import ProgressModal
except Exception:
    ProgressModal = None

OUTPUT_DIR = Path("triage_output")
OUTPUT_DIR.mkdir(exist_ok=True)

TRIAGE_CSV = OUTPUT_DIR / "triage_results.csv"
SAMPLE_DIR = OUTPUT_DIR / "ocr_samples"   # OCR text dumps land here for you to read

# ---------------------------------------------------------------------------
# TUNING KNOBS — these are the decisions that matter. Defaults are reasonable
# starting points; adjust after you see your first triage run.
# ---------------------------------------------------------------------------

# A page is "text-bearing" if it yields at least this many characters of
# extractable text. Scanned image-only pages yield ~0; born-digital pages yield
# hundreds to thousands. 50 is a deliberately low bar to catch sparse pages.
MIN_CHARS_PER_PAGE = 50

# A PDF is classified TEXT_READY if at least this fraction of its pages are
# text-bearing. Below this (but above zero) it's SUSPECT (partial/garbled layer);
# at zero it's NEEDS_OCR. 0.80 means "most pages have real text."
TEXT_READY_PAGE_FRACTION = 0.80

# Below this fraction (but > 0) => SUSPECT_TEXT_LAYER: it has *some* text but not
# enough to trust — could be a bad prior OCR, a cover-page-only text layer, or a
# mixed document. These get sampled too, because they're the trickiest bucket.
SUSPECT_FLOOR_FRACTION = 0.05

# To keep triage fast on thousands of files, only inspect up to this many pages
# per PDF (sampled across the document, not just the first N — a cover page can
# have text while the body is scanned). Set to 0 to inspect every page.
MAX_PAGES_TO_INSPECT = 12

# ---------------------------------------------------------------------------
# PHASE 1 — TRIAGE
# ---------------------------------------------------------------------------

def inspect_pdf(path: Path) -> dict:
    """Open a PDF, sample pages, measure how many bear extractable text.
    Returns a classification dict. Never raises on a bad file — records the
    error and moves on, because in 8,845 files some WILL be corrupt."""
    rec = {
        "path": str(path),
        "name": path.name,
        "pages_total": 0,
        "pages_inspected": 0,
        "pages_with_text": 0,
        "text_fraction": 0.0,
        "avg_chars_on_text_pages": 0.0,
        "bucket": "",
        "error": "",
    }
    try:
        doc = fitz.open(path)
    except Exception as e:
        rec["bucket"] = "ERROR_OPEN"
        rec["error"] = f"{type(e).__name__}: {e}"
        return rec

    try:
        n = doc.page_count
        rec["pages_total"] = n
        if n == 0:
            rec["bucket"] = "ERROR_EMPTY"
            doc.close()
            return rec

        # Choose which pages to inspect: spread across the document so a
        # text-bearing cover page can't mask a scanned body (and vice versa).
        if MAX_PAGES_TO_INSPECT and n > MAX_PAGES_TO_INSPECT:
            step = n / MAX_PAGES_TO_INSPECT
            page_indices = sorted({int(i * step) for i in range(MAX_PAGES_TO_INSPECT)})
        else:
            page_indices = range(n)

        text_pages = 0
        char_counts = []
        for i in page_indices:
            try:
                page = doc.load_page(i)
                text = page.get_text("text") or ""
            except Exception:
                text = ""
            chars = len(text.strip())
            rec["pages_inspected"] += 1
            if chars >= MIN_CHARS_PER_PAGE:
                text_pages += 1
                char_counts.append(chars)

        rec["pages_with_text"] = text_pages
        inspected = rec["pages_inspected"] or 1
        rec["text_fraction"] = round(text_pages / inspected, 3)
        rec["avg_chars_on_text_pages"] = round(
            sum(char_counts) / len(char_counts), 1) if char_counts else 0.0

        # Classify
        frac = rec["text_fraction"]
        if frac >= TEXT_READY_PAGE_FRACTION:
            rec["bucket"] = "TEXT_READY"
        elif frac <= SUSPECT_FLOOR_FRACTION:
            rec["bucket"] = "NEEDS_OCR"
        else:
            rec["bucket"] = "SUSPECT_TEXT_LAYER"

        doc.close()
    except Exception as e:
        rec["bucket"] = "ERROR_READ"
        rec["error"] = f"{type(e).__name__}: {e}"
        try:
            doc.close()
        except Exception:
            pass
    return rec


def run_triage(root: Path) -> None:
    pdfs = sorted(root.rglob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found under {root}")
        return
    print(f"Found {len(pdfs)} PDFs under {root}. Triaging (no OCR, fast)...\n")

    counts: dict[str, int] = {}
    rows = []
    pm = ProgressModal("PDF Triage (no OCR)", len(pdfs), "Classifying PDFs...") if ProgressModal else None
    for i, p in enumerate(pdfs, 1):
        rec = inspect_pdf(p)
        rows.append(rec)
        counts[rec["bucket"]] = counts.get(rec["bucket"], 0) + 1
        if pm and (i % 5 == 0 or i == len(pdfs)):
            pm.update(i, p.name)
            if pm.cancelled:
                print("\nCancelled via progress window.")
                break
        if i % 250 == 0 or i == len(pdfs):
            print(f"  [{i:>5}/{len(pdfs)}]  " +
                  "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if pm:
        pm.close("Triage complete")

    with TRIAGE_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    total = len(rows)
    print("\n" + "=" * 70)
    print("TRIAGE SUMMARY")
    print("=" * 70)
    for bucket, c in sorted(counts.items()):
        print(f"  {bucket:<20} {c:>6}  ({100*c/total:5.1f}%)")
    print(f"  {'TOTAL':<20} {total:>6}")
    print(f"\n  Detail written to: {TRIAGE_CSV}")

    needs = counts.get("NEEDS_OCR", 0) + counts.get("SUSPECT_TEXT_LAYER", 0)
    print(f"\n  → ~{needs} files will need OCR (NEEDS_OCR + SUSPECT).")
    print(f"  → ~{counts.get('TEXT_READY', 0)} files can skip OCR (extract text directly).")
    print(f"\n  NEXT: sample-OCR a few dozen of the NEEDS_OCR bucket and READ them:")
    print(f"        python {Path(sys.argv[0]).name} sample --n 30")


# ---------------------------------------------------------------------------
# PHASE 2 — OCR SAMPLING
# ---------------------------------------------------------------------------
#
# Pick ONE backend. Tesseract is the easy CPU baseline (good for a first look);
# PaddleOCR/docTR use your GPU and generally do better on real scans. The point
# of this phase is to READ the output and judge quality, so start with whatever
# installs fastest, look at the result, and upgrade the backend if it's rough.

OCR_BACKEND = "tesseract"   # "tesseract" | "paddleocr" | "doctr"


def ocr_page_image(pix) -> str:
    """OCR a single rendered page image (a fitz Pixmap) with the chosen backend."""
    if OCR_BACKEND == "tesseract":
        import os
        import shutil
        import pytesseract
        from PIL import Image
        # Make tesseract resolvable even when not on PATH (UB-Mannheim installer
        # does not add it by default): fall back to the standard install path.
        if not shutil.which("tesseract"):
            for cand in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                         r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
                if os.path.exists(cand):
                    pytesseract.pytesseract.tesseract_cmd = cand
                    break
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return pytesseract.image_to_string(img)

    elif OCR_BACKEND == "paddleocr":
        # PaddleOCR setup is heavier; this is a minimal call pattern.
        from paddleocr import PaddleOCR
        from PIL import Image
        import numpy as np
        global _PADDLE
        try:
            _PADDLE
        except NameError:
            _PADDLE = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        result = _PADDLE.ocr(np.array(img), cls=True)
        lines = []
        for block in (result or []):
            for line in (block or []):
                if line and len(line) > 1 and line[1]:
                    lines.append(line[1][0])
        return "\n".join(lines)

    elif OCR_BACKEND == "doctr":
        raise NotImplementedError(
            "docTR works on whole documents; wire it to your file paths directly "
            "rather than per-page pixmaps. Left as a stub on purpose.")
    else:
        raise ValueError(f"Unknown OCR_BACKEND: {OCR_BACKEND}")


def run_sample(n: int, dpi: int, max_pages_per_doc: int) -> None:
    if not TRIAGE_CSV.exists():
        print(f"ERR: no triage results at {TRIAGE_CSV}. Run 'triage' first.",
              file=sys.stderr)
        return
    SAMPLE_DIR.mkdir(exist_ok=True)

    with TRIAGE_CSV.open("r", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["bucket"] == "NEEDS_OCR"]
    if not rows:
        print("No NEEDS_OCR files in triage results — nothing to sample.")
        return

    # Even spread across the bucket, not just the first n (avoids sampling only
    # one folder / one document type).
    if len(rows) > n:
        step = len(rows) / n
        sample = [rows[int(i * step)] for i in range(n)]
    else:
        sample = rows

    print(f"OCR-sampling {len(sample)} of {len(rows)} NEEDS_OCR files "
          f"(backend={OCR_BACKEND}, dpi={dpi}, ≤{max_pages_per_doc} pages each).")
    print(f"Reading the output in {SAMPLE_DIR}/ is the WHOLE POINT — judge quality.\n")

    manifest = []
    pm = ProgressModal("OCR Sampling", len(sample), f"OCR backend: {OCR_BACKEND}") if ProgressModal else None
    for i, r in enumerate(sample, 1):
        path = Path(r["path"])
        out_txt = SAMPLE_DIR / f"{i:03d}__{path.stem}.txt"
        if pm:
            pm.update(i - 1, path.name)
            if pm.cancelled:
                print("\nCancelled via progress window.")
                break
        print(f"  [{i:>3}/{len(sample)}] {path.name} ...", end="", flush=True)
        try:
            doc = fitz.open(path)
            pages = min(doc.page_count, max_pages_per_doc)
            chunks = []
            for pno in range(pages):
                page = doc.load_page(pno)
                # Render at the chosen DPI. Higher dpi = better OCR, slower.
                pix = page.get_pixmap(dpi=dpi)
                text = ocr_page_image(pix)
                chunks.append(f"----- page {pno+1} -----\n{text}")
            doc.close()
            full = "\n\n".join(chunks)
            out_txt.write_text(full, encoding="utf-8")
            charcount = len(full)
            print(f" {charcount} chars -> {out_txt.name}")
            manifest.append({"sample_idx": i, "source": str(path),
                             "out": str(out_txt), "chars": charcount, "pages_ocrd": pages})
        except Exception as e:
            print(f" ERROR: {type(e).__name__}: {e}")
            manifest.append({"sample_idx": i, "source": str(path),
                             "out": "", "chars": 0, "error": str(e)})

    if pm:
        pm.close("Sampling complete")
    (SAMPLE_DIR / "sample_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    print("OCR SAMPLE DONE")
    print("=" * 70)
    print(f"  Text dumps: {SAMPLE_DIR}/  ({len(sample)} files)")
    print(f"\n  NOW DO THE IMPORTANT PART: open several .txt files and READ them.")
    print(f"  Ask yourself:")
    print(f"    - Are words/sentences intact, or garbled?")
    print(f"    - Did tables/columns survive, or interleave into nonsense?")
    print(f"    - Is enough meaning preserved that an embedding of this would")
    print(f"      retrieve correctly?")
    print(f"  If clean  -> proceed to full OCR + the RAG pipeline.")
    print(f"  If rough  -> switch OCR_BACKEND (try paddleocr on your GPU),")
    print(f"               raise --dpi, or preprocess images before scaling up.")


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="PDF triage + OCR sampling")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("triage", help="Phase 1: classify all PDFs (no OCR)")
    t.add_argument("root", help="folder containing the PDFs (searched recursively)")

    s = sub.add_parser("sample", help="Phase 2: OCR a sample of NEEDS_OCR files")
    s.add_argument("--n", type=int, default=30, help="how many files to sample")
    s.add_argument("--dpi", type=int, default=200,
                   help="render DPI for OCR (higher=better/slower; 200-300 typical)")
    s.add_argument("--max-pages", type=int, default=3,
                   help="max pages to OCR per sampled file (keep small for a quick look)")

    args = ap.parse_args()
    if args.cmd == "triage":
        run_triage(Path(args.root).expanduser())
    elif args.cmd == "sample":
        run_sample(args.n, args.dpi, args.max_pages)
    return 0


if __name__ == "__main__":
    sys.exit(main())
