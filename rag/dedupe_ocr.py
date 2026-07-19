"""
Stage 3 — de-duplicate the corpus for OCR + indexing.

The source PDFs contain byte-identical duplicates (same document cataloged under
several accession numbers / filenames). This script finds them (size group ->
MD5 confirm), keeps ONE copy per identical set, and:

  1. Writes the redundant stems to `stage3/ocr_dupe_skip.txt`. `chunk.py --source
     ocr` reads this and skips those `.md` files, so duplicate content never
     enters the index (non-destructive — the `.md` files stay on disk).
  2. Marks any still-`pending` redundant copies as `skip` in the OCR manifest so
     the batch doesn't waste time OCR'ing them (takes effect on the next
     resume; the running instance keeps its in-memory queue).

Keeper policy: the copy with the SHORTEST stem (the clean original name, e.g.
`no1-1982_PCRD-H001921` over `no1-1982_PCRD-H001921_PCRD-H004996`), ties by path.

Idempotent — safe to re-run (e.g. re-run after OCR finishes to refresh the list).

    cd C:\\Users\\jcvia\\PyCharmMiscProject\\ProjectY\\rag
    .\\.venv\\Scripts\\python.exe .\\dedupe_ocr.py
"""
from __future__ import annotations
import hashlib
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "ocr_out" / "manifest.sqlite"
SKIP_LIST = HERE / "stage3" / "ocr_dupe_skip.txt"


def md5(path: Path) -> str | None:
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()
    except OSError:
        return None


def main() -> int:
    if not DB.exists():
        print(f"ERR: no manifest at {DB}", file=sys.stderr)
        return 2
    con = sqlite3.connect(DB, timeout=30)
    rows = con.execute("SELECT path, stem, pages, status FROM files").fetchall()

    # size group -> md5 confirm -> identical-content sets
    by_size = defaultdict(list)
    for path, stem, pages, status in rows:
        try:
            sz = os.path.getsize(HERE / path)
        except OSError:
            sz = -1
        by_size[sz].append((path, stem, pages, status))

    dupsets = []
    for sz, group in by_size.items():
        if sz <= 0 or len(group) < 2:
            continue
        by_hash = defaultdict(list)
        for rec in group:
            h = md5(HERE / rec[0])
            if h:
                by_hash[h].append(rec)
        for h, files in by_hash.items():
            if len(files) > 1:
                dupsets.append(files)

    skip_stems = []
    pending_skip_paths = []
    for files in dupsets:
        keep = min(files, key=lambda r: (len(r[1]), r[0]))  # shortest stem, then path
        for path, stem, pages, status in files:
            if (path, stem) == (keep[0], keep[1]):
                continue
            skip_stems.append(stem)
            if status == "pending":
                pending_skip_paths.append(path)

    skip_stems = sorted(set(skip_stems))
    SKIP_LIST.parent.mkdir(parents=True, exist_ok=True)
    SKIP_LIST.write_text("\n".join(skip_stems) + ("\n" if skip_stems else ""), encoding="utf-8")

    marked = 0
    for path in pending_skip_paths:
        cur = con.execute("UPDATE files SET status='skip', updated=strftime('%s','now') "
                          "WHERE path=? AND status='pending'", (path,))
        marked += cur.rowcount
    con.commit()
    con.close()

    red_pages = sum(f[0][2] * (len(f) - 1) for f in dupsets)
    print("=" * 64)
    print("OCR DE-DUPLICATION")
    print("=" * 64)
    print(f"  identical-content sets : {len(dupsets)}")
    print(f"  redundant copies       : {len(skip_stems)}  (~{red_pages:,} pages)")
    print(f"  skip-list written      : {SKIP_LIST}  ({len(skip_stems)} stems)")
    print(f"  pending copies -> skip : {marked} (won't be OCR'd on next resume)")
    print(f"\n  chunk.py --source ocr will now exclude these stems from the index.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
