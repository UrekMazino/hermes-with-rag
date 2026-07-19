# Project Y - llama.cpp server launcher (native Windows, CUDA)
# Serves Qwen3 30B A3B on RTX 4080 Super for Hermes Agent + Roo Code.
$ErrorActionPreference = "Stop"
$bin   = "C:\Users\jcvia\PyCharmMiscProject\ProjectY\llama-cpp\llama-server.exe"
# A/B model toggle — only ONE fits in 16 GB, so swap by commenting/uncommenting.
# Thinking = better reasoning on hard/multi-step tasks (slower; emits <think>).
# Instruct = faster, no reasoning preamble.
# Instruct chosen 2026-07-08: fast (~2s) + deterministic tool-calling for the RAG/agent role.
# Thinking derailed on tool calls (looped search_files 145x / ~113s per call) — see RAG_PLAN doc.
# $model = "C:\Users\jcvia\PyCharmMiscProject\ProjectY\models\Qwen3-30B-A3B-Thinking-2507-Q4_K_M.gguf"
$model = "C:\Users\jcvia\PyCharmMiscProject\ProjectY\models\Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf"

# --n-cpu-moe: number of MoE expert layers kept on CPU (RAM). The 4080 Super has
# 16 GB; Q4 weights are ~17 GB AND Hermes needs a 64K context (large KV cache),
# so we push more expert layers to RAM to free VRAM. Raise if you OOM, lower for speed.
$nCpuMoe = 26

# Hermes Agent requires >= 64K context. Qwen3-30B-A3B trained on 256K, so 64K is safe.
# KV cache is quantized to q8_0 (needs flash attention) to roughly halve its VRAM cost.
$argList = @(
  '--model', $model,
  '--alias', 'qwen3-30b',
  # Port is overridable: WSL2's localhost forwarding hijacks 127.0.0.1:<port> whenever
  # something inside WSL listens on the same port — it wins even when llama-server has
  # legitimately bound it on Windows. Open WebUI (a container in Ubuntu) sits on 8080, so
  # 8080 silently answers with Open WebUI whenever llama-server isn't up. Set
  # $env:LLAMA_PORT to move llama-server off it.
  '--host', '127.0.0.1', '--port', "$(if ($env:LLAMA_PORT) { $env:LLAMA_PORT } else { '8080' })",
  '-ngl', '99',
  '--n-cpu-moe', "$nCpuMoe",
  '-c', '65536',
  '--parallel', '1',
  '-fa', 'on',
  '--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0',
  '--jinja',
  # 2026-07-09: temp 0.3 (NOT greedy 0). With traps removed the wandering is gone, and
  # greedy temp 0 landed on a degenerate path where Qwen emitted `search_docs(...)` as a
  # TEXT code-snippet instead of a real tool call. temp>=0.2 reliably emits real tool
  # calls (proven) while staying low enough for deterministic tool selection.
  '--temp', '0.3'
)
# Launch DETACHED in its own hidden console. Running `& $bin ...` in this console
# makes llama-server die with STATUS_CONTROL_C_EXIT (-1073741510) when the launching
# console closes (e.g. when started by the logon Scheduled Task) — this is why it
# kept stopping. A separate hidden console isolates it from parent console signals.
Start-Process -FilePath $bin -ArgumentList $argList -WindowStyle Hidden
Write-Output "llama-server launched (detached). Model loads in ~10-15s."
