# Project Y - RAG HTTP gateway launcher (native Windows)
# Serves the eLibrary "AI mode" endpoint (rag/rag_api.py) on 127.0.0.1:8090.
#
# Reuses the same retrieval stack as the Hermes docs MCP server (hybrid dense+FTS +
# reranker, all on CPU) and synthesises answers via the local Qwen (llama-server on
# :8080). Laravel (eLibrary) is the only caller; it talks to this server-side with a
# shared bearer token and never exposes it to the browser.
#
# Prereqs: llama-server running (start-llama-server.ps1) and the LanceDB index built.
$ErrorActionPreference = "Stop"

$root = "C:\Users\jcvia\PyCharmMiscProject\ProjectY"
$py   = "$root\rag\.venv\Scripts\python.exe"

# The shared secret Laravel authenticates with. MUST match HERMES_GATEWAY_TOKEN in the
# eLibrary .env. Kept out of source: set it in the environment, or paste it here on the
# server (and keep this file out of any public repo).
if (-not $env:HERMES_GATEWAY_TOKEN) {
    # Fallback: read it from the eLibrary .env so the two can't drift.
    $envFile = "C:\laragon\www\elibrary\.env"
    if (Test-Path $envFile) {
        $line = Select-String -Path $envFile -Pattern '^HERMES_GATEWAY_TOKEN=(.+)$' | Select-Object -First 1
        if ($line) { $env:HERMES_GATEWAY_TOKEN = $line.Matches[0].Groups[1].Value.Trim() }
    }
}
if (-not $env:HERMES_GATEWAY_TOKEN) {
    Write-Error "HERMES_GATEWAY_TOKEN is not set (env or eLibrary .env). Refusing to start open."
    exit 1
}

Push-Location "$root\rag"
try {
    # --host 127.0.0.1 : localhost only, never public (RAG_INTEGRATION_PLAN.md §10.3).
    & $py -m uvicorn rag_api:app --host 127.0.0.1 --port 8090
}
finally {
    Pop-Location
}
