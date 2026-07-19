# Project Y

A fully-local AI workstation powered by Qwen3-30B-A3B, hosted on an RTX 4080 SUPER with 16 GB VRAM. This stack runs the Qwen3-30B model with full 64K context using a custom llama.cpp server with memory-optimized configuration, enabling advanced local AI workflows with Hermes Agent and Roo Code.

> **Rebuilding this on a new machine / server?** Start with **[`DEPLOYMENT_RUNBOOK.md`](DEPLOYMENT_RUNBOOK.md)** —
> the single ordered guide (Hermes → llama.cpp → lockdown → RAG → eLibrary → catalog sync) with a
> doc index across both repos. This README covers day-to-day serving.

## Architecture

The system follows a modular architecture:

1. **Roo Code** — The AI coding assistant that interacts with the local model.
2. **Hermes Agent** — The orchestrator and CLI interface for AI workflows.
3. **llama.cpp Server** — The local inference server serving the Qwen3-30B model.
4. **Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf** — The quantized model file.
5. **NVIDIA GeForce RTX 4080 SUPER** — The GPU with 16 GB VRAM providing the compute power.

This configuration uses clever offloading (MoE layers to RAM) and quantization to run the large model within VRAM constraints.

## How to Start

### 1. Launch the llama.cpp Server
Run the server script to load the model into GPU memory:
```powershell
.\start-llama-server.ps1
```
This starts `llama-server.exe` with:
- `--model` → `Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf`
- `--host 0.0.0.0 --port 8080`
- `--n-cpu-moe 26` (offloads 26 MoE layers to RAM to save VRAM)
- `--cache-type-k q8_0 --cache-type-v q8_0` (half-size KV cache)
- `-c 65536` (64K context)
- `-fa on` (Flash Attention for efficiency)

> 🔴 **Wait 30+ seconds** — the model loads fully into VRAM before the server becomes ready.

### 2. Launch Hermes Agent
Once the server is ready (`llama-server.exe` output shows `Listening on 0.0.0.0:8080`), run:
```powershell
.\start-hermes.ps1
```
This:
- Sets up Git Bash in the `PATH` (to handle Windows paths).
- Adds `ripgrep` to `PATH` for `search_files` tool.
- Launches the Hermes agent CLI.

## Model and Key Server Flags

| Flag | Purpose | Value | Reason |
|------|---------|-------|--------|
| `--model` | Model file | `Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf` | 30B model, Q4_K_M quantization, trained on 256K context |
| `--n-cpu-moe` | MoE layer offload | `26` | 16 GB VRAM + 64K context → need to move layers to RAM |
| `--cache-type-k/v q8_0` | KV cache quantization | `q8_0` | Halves VRAM cost |
| `-c 65536` | Context length | `65536` | Required for Hermes Agent |
| `-fa on` | Flash Attention | `on` | Reduces memory usage and speeds up inference |
| `--parallel 1` | Inference mode | `1` | Ensures stability on 4080 SUPER |

## GPU

- **Model**: NVIDIA GeForce RTX 4080 SUPER
- **VRAM**: 16 GB
- **CUDA**: Supported

> ✅ This stack runs the Qwen3-30B model with full 64K context on 16 GB VRAM using clever memory management.

## Troubleshooting

> 🛠 **Always launch Hermes via `start-hermes.ps1`** — it sets up the correct `PATH` for Git Bash and `ripgrep`.

If `start-hermes.ps1` fails with:
- `The term 'hermes' is not recognized` or `No module named hermes`
- `bash.exe: hermes: command not found`

👉 **Solution**: The `hermes.exe` Python binary is in `C:\Users\jcvia\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe`. Ensure the `PATH` is set correctly in `start-hermes.ps1`.

> ✅ **Fix**: Confirm that the script runs `hermes.exe` from its correct location. The script already does this via:
> ```powershell
> $env:PATH = "C:\Program Files\Git\bin;" + $env:PATH
> & "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\hermes.exe" @args
> ```
> If still failing, manually verify the file exists at that path.