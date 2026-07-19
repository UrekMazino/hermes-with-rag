# Project Y — Stage 3 OCR batch launcher (resumable Marker OCR of the scanned bucket).
# Self-guarding: if nothing is pending, it exits WITHOUT touching llama-server (so a logon-
# triggered Scheduled Task won't keep killing Hermes after OCR is finished). Otherwise it stops
# llama-server to free the GPU and runs ocr_run.py --tier all (ascending; skips done files).
# Fully resumable via the SQLite manifest. Logs append to rag\ocr_out\ocr_run.log.
# Run from your OWN terminal or a Scheduled Task so it survives independent of Claude Code:
#     powershell -ExecutionPolicy Bypass -File C:\Users\jcvia\PyCharmMiscProject\ProjectY\start-ocr-batch.ps1
$ErrorActionPreference = "SilentlyContinue"
$rag = "C:\Users\jcvia\PyCharmMiscProject\ProjectY\rag"
$py  = "$rag\.venv\Scripts\python.exe"

# Skip everything if the batch is already complete (keeps Hermes online once OCR is done).
$pending = (& $py "$rag\ocr_run.py" --pending 2>$null | Select-Object -Last 1)
if ("$pending".Trim() -eq "0") { "OCR complete - nothing pending; leaving llama-server alone." | Out-File -Append "$rag\ocr_out\ocr_run.log"; exit 0 }

Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
& $py "$rag\ocr_run.py" --tier all --no-modal *>> "$rag\ocr_out\ocr_run.log"
