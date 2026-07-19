"""
Stage 3 — resumable, tiered Marker OCR over the scanned bucket.

OCRs every NEEDS_OCR + SUSPECT_TEXT_LAYER file (from triage_results.csv) with
Marker (Surya), writing structured **Markdown** to `ocr_out/<stem>.md`. Progress
+ status is tracked in a **SQLite manifest** (`ocr_out/manifest.sqlite`) so the
run is fully **resumable** — re-running skips files already `done` and continues.

Tiered: processes smaller documents first (quick, broad coverage), giant docs
last, so an interrupted run still indexed most of the corpus. Order within a
tier is ascending by page count.

GPU: Marker uses the GPU. **STOP llama-server first** (frees the full 16 GB;
avoids OOM):
    Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force

Run it in your own terminal (progress modal shows). Safe to Ctrl-C and resume.
    cd C:\\Users\\jcvia\\PyCharmMiscProject\\ProjectY\\rag
    .\\.venv\\Scripts\\python.exe .\\ocr_run.py --tier small      # 1-10 pp first
    .\\.venv\\Scripts\\python.exe .\\ocr_run.py --tier medium     # 11-50 pp
    .\\.venv\\Scripts\\python.exe .\\ocr_run.py --tier large      # 51+ pp (overnight)
    .\\.venv\\Scripts\\python.exe .\\ocr_run.py --tier all        # everything, ascending
Options: --limit N (stop after N files), --status (print manifest summary & exit).
"""
from __future__ import annotations
import argparse
import csv
import sqlite3
import sys
import time
from pathlib import Path

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
TRIAGE_CSV = HERE / "triage_output" / "triage_results.csv"
OUT_DIR = HERE / "ocr_out"
DB_PATH = OUT_DIR / "manifest.sqlite"
OCR_BUCKETS = ("NEEDS_OCR", "SUSPECT_TEXT_LAYER")

TIERS = {
    "small":  (1, 10),
    "medium": (11, 50),
    "large":  (51, 10**9),
    "all":    (1, 10**9),
}


def db_connect():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS files(
        path TEXT PRIMARY KEY, stem TEXT, pages INTEGER, bucket TEXT,
        status TEXT DEFAULT 'pending', out TEXT, chars INTEGER,
        seconds REAL, error TEXT, updated REAL)""")
    conn.commit()
    return conn


def seed_worklist(conn):
    if not TRIAGE_CSV.exists():
        print(f"ERR: no triage results at {TRIAGE_CSV}", file=sys.stderr)
        sys.exit(2)
    n = 0
    with TRIAGE_CSV.open("r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["bucket"] not in OCR_BUCKETS:
                continue
            try:
                pages = int(r["pages_total"])
            except Exception:
                pages = 0
            cur = conn.execute("INSERT OR IGNORE INTO files(path,stem,pages,bucket) VALUES(?,?,?,?)",
                               (r["path"], Path(r["path"]).stem, pages, r["bucket"]))
            n += cur.rowcount
    conn.commit()
    return n


def print_status(conn):
    rows = conn.execute("SELECT status, COUNT(*), COALESCE(SUM(pages),0) FROM files GROUP BY status").fetchall()
    print("Manifest status:")
    for st, c, pg in rows:
        print(f"  {st:<8} {c:>6} files  {pg:>9,} pages")
    tot = conn.execute("SELECT COUNT(*), COALESCE(SUM(pages),0) FROM files").fetchone()
    print(f"  {'TOTAL':<8} {tot[0]:>6} files  {tot[1]:>9,} pages")


def out_path_for(stem: str) -> Path:
    return OUT_DIR / f"{stem}.md"


def main() -> int:
    ap = argparse.ArgumentParser(description="Stage 3 resumable Marker OCR")
    ap.add_argument("--tier", choices=list(TIERS), default="small")
    ap.add_argument("--limit", type=int, default=0, help="stop after N files (0 = no limit)")
    ap.add_argument("--status", action="store_true", help="print manifest summary and exit")
    ap.add_argument("--pending", action="store_true", help="print the pending file count and exit")
    ap.add_argument("--no-modal", action="store_true",
                    help="disable the GUI progress modal (for detached/headless runs; logs to console)")
    args = ap.parse_args()

    conn = db_connect()
    seeded = seed_worklist(conn)
    if seeded:
        print(f"Seeded {seeded} new files into the manifest.")
    if args.pending:
        print(conn.execute("SELECT COUNT(*) FROM files WHERE status='pending'").fetchone()[0])
        return 0
    if args.status:
        print_status(conn)
        return 0

    lo, hi = TIERS[args.tier]
    pending = conn.execute(
        "SELECT path, stem, pages FROM files WHERE status='pending' AND pages>=? AND pages<=? "
        "ORDER BY pages ASC", (lo, hi)).fetchall()
    if args.limit:
        pending = pending[:args.limit]
    if not pending:
        print(f"No pending files in tier '{args.tier}' ({lo}-{hi} pp). Nothing to do.")
        print_status(conn)
        return 0

    total_pages = sum(p for _, _, p in pending)
    print(f"Tier '{args.tier}': {len(pending)} files / {total_pages:,} pages to OCR -> {OUT_DIR}/")
    print("(Ctrl-C is safe — progress is checkpointed; re-run to resume.)\n")

    # Single-instance lock (PID-liveness based) so a Scheduled-Task run and a manual run can't
    # double-process the manifest. A dead PID (e.g. after a crash/teardown) is ignored, so resume
    # is never blocked.
    import os
    import ctypes
    lock = OUT_DIR / "ocr_run.lock"

    def _alive(pid: int) -> bool:
        try:
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return code.value == 259  # STILL_ACTIVE
        except Exception:
            return False

    if lock.exists():
        try:
            other = int(lock.read_text().strip())
        except Exception:
            other = None
        if other and other != os.getpid() and _alive(other):
            print(f"Another ocr_run (PID {other}) is active; exiting to avoid double-run.", file=sys.stderr)
            return 0
    lock.write_text(str(os.getpid()))

    # Load Marker once.
    import torch
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered
    from marker.config.parser import ConfigParser
    if not torch.cuda.is_available():
        print("WARNING: CUDA not available — OCR will be very slow.", file=sys.stderr)
    print("Loading Surya models...", flush=True)
    models = create_model_dict()
    # paginate_output inserts a page separator "\n\n{PAGE_ID}" + ("-"*48) + "\n\n"
    # (PAGE_ID 0-indexed) so chunk.py can attribute chunks to pages for citations.
    cfg = ConfigParser({"output_format": "markdown", "paginate_output": True})
    converter = PdfConverter(config=cfg.generate_config_dict(), artifact_dict=models,
                             processor_list=cfg.get_processors(), renderer=cfg.get_renderer())

    pm = ProgressModal(f"Stage 3 OCR — tier '{args.tier}'", len(pending),
                       f"{total_pages:,} pages, Marker/Surya") if (ProgressModal and not args.no_modal) else None
    done = errors = pages_done = 0
    t_start = time.time()
    for i, (path, stem, pages) in enumerate(pending, 1):
        if pm:
            pm.update(i - 1, f"{stem} ({pages}pp)")
            if pm.cancelled:
                print("\nCancelled via progress window (progress saved).")
                break
        out = out_path_for(stem)
        print(f"  [{i:>4}/{len(pending)}] {stem} ({pages}pp) ...", end="", flush=True)
        t0 = time.time()
        try:
            text, _, _ = text_from_rendered(converter(str(HERE / path)))
            out.write_text(text, encoding="utf-8")
            dt = time.time() - t0
            conn.execute("UPDATE files SET status='done', out=?, chars=?, seconds=?, error=NULL, updated=? "
                         "WHERE path=?", (str(out), len(text), round(dt, 1), time.time(), path))
            conn.commit()
            done += 1
            pages_done += pages
            print(f" {len(text)} chars ({dt:.1f}s)")
        except Exception as e:
            dt = time.time() - t0
            conn.execute("UPDATE files SET status='error', seconds=?, error=?, updated=? WHERE path=?",
                         (round(dt, 1), f"{type(e).__name__}: {e}", time.time(), path))
            conn.commit()
            errors += 1
            print(f" ERROR: {type(e).__name__}: {e}")

    if pm:
        pm.close("OCR tier complete")
    try:
        lock.unlink()
    except Exception:
        pass
    elapsed = time.time() - t_start
    rate = pages_done / elapsed if elapsed > 0 else 0
    print("\n" + "=" * 70)
    print(f"OCR TIER '{args.tier}' DONE this run: {done} ok, {errors} errors, {pages_done:,} pages "
          f"in {elapsed/60:.1f} min ({rate:.2f} pages/s)")
    print_status(conn)
    print(f"\n  Next: chunk OCR markdown -> embed -> index (chunk.py --source ocr_out)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
