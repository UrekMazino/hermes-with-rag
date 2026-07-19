"""
Stage 1c — Marker (Surya) OCR comparison on the SAME 30 files Tesseract sampled.

Reads `triage_output/ocr_samples/sample_manifest.json` (produced by
`pdf_triage_and_sample.py sample`) and re-OCRs each of those exact source PDFs
with Marker, writing structured **Markdown** to `triage_output/marker_samples/`.
Each file is limited to the same page range Tesseract used (`pages_ocrd`) so the
two outputs are directly comparable.

Why: the Tesseract baseline showed a layout/reading-order weakness (figure
captions spliced into body sentences). Marker does region detection (body /
figure / caption / table) and should keep them separate. This run is the
evidence to pick the production OCR engine before the big Stage 3 batch.

GPU note: Marker uses the GPU. STOP llama-server before running so it has the
full 16 GB (otherwise it may OOM):
    Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force

Run it in your OWN terminal so the progress modal shows:
    cd C:\\Users\\jcvia\\PyCharmMiscProject\\ProjectY\\rag
    .\\.venv\\Scripts\\python.exe .\\marker_sample.py
Optional:  --max-pages N   (cap pages per file; default = match Tesseract sample)
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

# UTF-8 console (the corpus has non-cp1252 glyphs)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from progress_modal import ProgressModal
except Exception:
    ProgressModal = None

HERE = Path(__file__).resolve().parent
SAMPLE_MANIFEST = HERE / "triage_output" / "ocr_samples" / "sample_manifest.json"
OUT_DIR = HERE / "triage_output" / "marker_samples"


def build_converter():
    """Load Marker models once (expensive). Returns (converter, text_from_rendered)."""
    import torch
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered

    dev = "CUDA" if torch.cuda.is_available() else "CPU (no CUDA — will be slow)"
    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "-"
    print(f"Marker device: {dev}  {gpu}")
    print("Loading Surya models (first run downloads weights, ~minutes)...", flush=True)
    models = create_model_dict()
    return models, PdfConverter, text_from_rendered


def make_page_converter(models, PdfConverter, last_page: int):
    """A PdfConverter limited to pages 0..last_page (inclusive), markdown out."""
    from marker.config.parser import ConfigParser
    cfg = {"output_format": "markdown", "page_range": f"0-{max(0, last_page)}"}
    cp = ConfigParser(cfg)
    return PdfConverter(
        config=cp.generate_config_dict(),
        artifact_dict=models,
        processor_list=cp.get_processors(),
        renderer=cp.get_renderer(),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Marker OCR comparison on the sampled files")
    ap.add_argument("--max-pages", type=int, default=0,
                    help="cap pages per file (0 = match the Tesseract sample's page count)")
    args = ap.parse_args()

    if not SAMPLE_MANIFEST.exists():
        print(f"ERR: no sample manifest at {SAMPLE_MANIFEST}.\n"
              f"     Run 'pdf_triage_and_sample.py sample' first.", file=sys.stderr)
        return 2
    items = json.loads(SAMPLE_MANIFEST.read_text(encoding="utf-8"))
    items = [it for it in items if it.get("source")]
    if not items:
        print("No usable entries in sample manifest.", file=sys.stderr)
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    models, PdfConverter, text_from_rendered = build_converter()

    print(f"\nMarker-OCR comparing {len(items)} files -> {OUT_DIR}/\n")
    manifest = []
    pm = ProgressModal("Marker OCR (Surya) comparison", len(items),
                       "Same 30 files as the Tesseract sample") if ProgressModal else None

    for i, it in enumerate(items, 1):
        src = (HERE / it["source"]).resolve()
        idx = it.get("sample_idx", i)
        out_md = OUT_DIR / f"{idx:03d}__{src.stem}.md"
        if pm:
            pm.update(i - 1, src.name)
            if pm.cancelled:
                print("\nCancelled via progress window.")
                break
        # match Tesseract's page coverage unless overridden
        pages = int(it.get("pages_ocrd") or 1)
        if args.max_pages > 0:
            pages = min(pages, args.max_pages)
        last_page = max(0, pages - 1)

        print(f"  [{i:>3}/{len(items)}] {src.name} (pages 1-{pages}) ...", end="", flush=True)
        t0 = time.time()
        try:
            conv = make_page_converter(models, PdfConverter, last_page)
            rendered = conv(str(src))
            text, _ext, _imgs = text_from_rendered(rendered)
            out_md.write_text(text, encoding="utf-8")
            dt = time.time() - t0
            print(f" {len(text)} chars -> {out_md.name}  ({dt:.1f}s)")
            manifest.append({"sample_idx": idx, "source": str(src),
                             "out": str(out_md), "chars": len(text),
                             "pages": pages, "seconds": round(dt, 1)})
        except Exception as e:
            print(f" ERROR: {type(e).__name__}: {e}")
            manifest.append({"sample_idx": idx, "source": str(src),
                             "out": "", "chars": 0, "pages": pages,
                             "error": f"{type(e).__name__}: {e}"})

    if pm:
        pm.close("Marker comparison complete")
    (OUT_DIR / "marker_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    ok = [m for m in manifest if m["chars"] > 0]
    print("\n" + "=" * 70)
    print("MARKER COMPARISON DONE")
    print("=" * 70)
    print(f"  Markdown dumps: {OUT_DIR}/  ({len(ok)}/{len(items)} ok)")
    print(f"  Compare side-by-side against triage_output/ocr_samples/*.txt")
    print(f"  Focus on the layout cases — e.g. 030 (caption splice), 002 (title pages).")
    print(f"  Ask: did Marker keep captions/columns/tables SEPARATE from body prose?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
