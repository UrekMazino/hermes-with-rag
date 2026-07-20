# Project Y - catalog-sync worker launcher (native Windows)
#
# Drains the eLibrary `rag_sync_queue` outbox into LanceDB on a loop, so records created/edited/
# deleted/published in the eLibrary become searchable by AI mode without anyone running the worker
# by hand. See CATALOG_SYNC_RUNBOOK.md / MANUAL_REINDEX_PROCEDURE.md.
#
# --cpu: embeds on CPU so it coexists with a serving llama-server (no VRAM contention). Metadata
#        records embed in well under a second; scanned PDFs still need the Stage-3 OCR batch.
#
# Prereqs: MySQL/Laragon up (the queue lives in the eLibrary DB) and the LanceDB index built.
# Does NOT need llama-server — embedding is local (BGE-M3); llama-server only answers questions.
$ErrorActionPreference = "Stop"

$root = "C:\Users\jcvia\PyCharmMiscProject\ProjectY"
$py   = "$root\rag\.venv\Scripts\python.exe"

Push-Location "$root\rag"
try {
    & $py rag_sync_worker.py --loop --interval 30 --cpu
}
finally {
    Pop-Location
}
