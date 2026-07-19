# Project Y — Inference Server Spec (Llama-3.3-70B)

_Created 2026-06-26._

Single-box, fully-local inference server to serve **Llama-3.3-70B-Instruct** to **~100 users/day**,
with the RAG embeddings + reranker co-located. Replaces nothing yet — this is the procurement/build
reference. Hermes points its model provider at this box's OpenAI-compatible endpoint (same pattern as
the current Qwen/llama.cpp setup). The existing **RTX 4080 Super** box becomes dev/secondary.

> **Decision:** chosen **Llama-3.3-70B** over Qwen3-235B because 70B fits **fully on one GPU** (fast,
> good concurrency), whereas 235B needs CPU/RAM offload on this hardware (smarter but slower). See
> "Alternatives considered."

---

## 1. Scale & capacity assumptions
```
100 users/day × ~8 requests   = ~800 requests/day
800 / 86,400 s                ≈ 0.009 req/s   (~1 request/minute average)
peak concurrency              ≈ 1–3 simultaneous generations (often 0)
peak token demand             ≈ 100–300 tok/s aggregate
```
Low-traffic internal-tool load → **memory-bound, not throughput-bound**. One GPU that holds the model
is plenty; no multi-GPU serving cluster needed. Plenty of burst headroom (see §5).

---

## 2. The build (parts list)

| Component | Recommended | Budget alternative | ~USD |
|---|---|---|---|
| **GPU** | **RTX PRO 6000 Blackwell 96 GB** | RTX 6000 Ada 48 GB (−$1,700) | $8,500 |
| **CPU** | Threadripper 7960X (24c) | Ryzen 9 7950X (16c, −$850) | $1,400 |
| **Motherboard** | sTR5 / TRX50 workstation | X670E (AM5) | $700 |
| **RAM** | 256 GB DDR5 ECC (8×32) | 128 GB (−$600) | $1,200 |
| **Storage** | 2 TB Gen4 NVMe (OS+models) + 4 TB NVMe (corpus/LanceDB) | — | $500 |
| **PSU** | 1500 W 80+ Platinum | 1000 W (if Ada GPU) | $350 |
| **Case + cooling** | Full tower, high airflow, air-cooled | — | $400 |
| **NIC** | 10 GbE (often onboard) | 1 GbE (fine for text) | $100 |
| | | **Blackwell build total** | **~$13,150** |
| | | **Ada 48 GB + Ryzen total** | **~$9,300** |

Notes:
- **CPU/RAM are not the bottleneck** for 70B (it's GPU-resident) — the workstation CPU/ECC is for a
  reliable always-on server and the option to add a 2nd GPU later.
- **1 GbE genuinely suffices** (LLM responses are small text); 10 GbE is headroom.
- For 70B you do **not** need 256 GB RAM; 128 GB is fine. The 256 GB is future-proofing (e.g. trying a
  big-MoE via offload later — see Alternatives).

---

## 3. GPU rationale
| Card | VRAM | Runs Llama-3.3-70B at | KV/context headroom |
|---|---|---|---|
| RTX 6000 Ada | 48 GB | **4-bit (AWQ/GPTQ)** ~40 GB | ~8 GB → 8–16K ctx, a few concurrent |
| **RTX PRO 6000 Blackwell** ⭐ | 96 GB | **FP8** ~70 GB (better quality) | ~26 GB → 32K+ ctx, many concurrent |

For 100 users/day the **48 GB Ada is sufficient** and fits a tighter budget. The **96 GB Blackwell** is
worth ~+$1,700: **FP8** (noticeably better than 4-bit), holds LLM + embeddings + reranker with room to
spare, larger context, and future-proofs to bigger models. **Recommended: Blackwell 96 GB.**

---

## 4. Software / serving stack
- **OS:** Ubuntu 24.04 LTS · NVIDIA driver · CUDA 12.x.
- **Serving engine:** **vLLM** (or SGLang) — continuous batching, paged KV cache, **prefix caching**
  (big win for RAG: repeated system prompt + retrieved context is cached across requests),
  OpenAI-compatible API. *(NOT llama.cpp `--parallel 1` — that's single-stream.)*
- **Model:** `meta-llama/Llama-3.3-70B-Instruct`
  - **96 GB Blackwell:** FP8 (`--quantization fp8`).
  - **48 GB Ada:** a pre-quantized **AWQ 4-bit** checkpoint (`--quantization awq_marlin`).
  - Swap-compatible alternative on identical hardware: **Qwen2.5/3-72B-Instruct**.
- **RAG co-located:** BGE-M3 + bge-reranker-v2-m3 (FlagEmbedding) on the same box (CPU or a sliver of
  GPU) — trivial at 100 users/day.
- **Front door:** **nginx** for auth/rate-limit in front of the endpoint (reuse Project Y's nginx
  experience).

### Example vLLM launch (96 GB Blackwell, FP8)
```bash
vllm serve meta-llama/Llama-3.3-70B-Instruct \
  --quantization fp8 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.92 \
  --enable-prefix-caching \
  --host 127.0.0.1 --port 8000 \
  --api-key sk-local
```
### 48 GB Ada variant (AWQ 4-bit)
```bash
vllm serve <user>/Llama-3.3-70B-Instruct-AWQ \
  --quantization awq_marlin \
  --max-model-len 16384 \
  --gpu-memory-utilization 0.95 \
  --enable-prefix-caching \
  --host 127.0.0.1 --port 8000 --api-key sk-local
```

---

## 5. Capacity for this workload
- **Single-stream:** ~40–60 tok/s on 70B (snappy for one user).
- **Batched aggregate (vLLM):** several hundred tok/s across concurrent requests.
- **100 users/day** needs only ~100–300 tok/s peak → **large headroom**. The 96 GB config absorbs bursts
  of **~10–30 simultaneous** generations; the 48 GB handles a handful at once.
- **Quality:** a real step up from the current Qwen3-30B — should meaningfully reduce the numeric-
  fabrication issues measured in `rag/RAG_TEST_QUESTIONS.md` (bigger model → better instruction-following
  & faithfulness), while staying fully local.

---

## 6. Integration with Project Y
1. Build box → Ubuntu/CUDA/vLLM → serve Llama-3.3-70B on `127.0.0.1:8000` (OpenAI-compatible).
2. Put BGE-M3 + reranker on this box (or keep on the 4080 box).
3. Point **Hermes** primary model at the new endpoint — same edit as the current Qwen provider in
   `%LOCALAPPDATA%\hermes\config.yaml` (`base_url: http://<server>:8000/v1`, `model:
   llama-3.3-70b`, `api_key: sk-local`). Restart the gateway.
4. Keep **funded Opus** as the small **top-tier router** for the hardest correctness-critical queries
   (~$50–200/month at this volume — see economics).
5. The **RTX 4080 Super** stays as dev/secondary / fallback.

---

## 7. Build & setup checklist
- [ ] Procure parts (GPU is the long-lead item).
- [ ] Assemble; confirm PSU headroom (Blackwell GPU ~600 W; total peak ~1.0–1.1 kW).
- [ ] Install Ubuntu 24.04 LTS + NVIDIA driver + CUDA 12.x; verify `nvidia-smi`.
- [ ] `pip install vllm` (matching CUDA); smoke-test with a small model.
- [ ] Pull Llama-3.3-70B (FP8 for 96 GB, AWQ for 48 GB); launch vLLM (§4); test `/v1/chat/completions`.
- [ ] Stand up nginx (auth/rate-limit) in front of `:8000`.
- [ ] Move BGE-M3 + reranker over; re-point `rag_mcp_server.py` if embeddings move.
- [ ] Re-point Hermes provider; restart gateway; run the 17-Q set in `rag/RAG_TEST_QUESTIONS.md` to
      compare 70B vs the 30B baseline.
- [ ] (Optional) Wire the cheap-model/Opus router for correctness-critical queries.

---

## 8. Budget knobs
- **Cheapest viable (~$9.3k):** RTX 6000 Ada 48 GB + Ryzen 9 7950X + 128 GB → Llama-3.3-70B 4-bit. Fine
  for 100 users/day.
- **Recommended (~$13k):** Blackwell 96 GB build → 70B FP8, headroom, future-proof.
- **Do NOT** buy dual-GPU/EPYC for this scale — overkill for 100 users/day.

---

## 9. Alternatives considered
- **Qwen3-235B-A22B on the same box:** does **not** fit a single GPU's VRAM (needs ~120 GB at 4-bit,
  ~235 GB FP8). Runs only via **GPU + system RAM offload** (llama.cpp `--n-cpu-moe` or **KTransformers**)
  at **~15–30 tok/s** on the 96 GB + 256–384 GB RAM build — *smarter but slower, thinner concurrency*.
  Viable for 100 users/day if you accept the speed hit; would bump RAM to **384 GB**. Chose 70B for
  speed + clean vLLM batching. To run 235B *fast* (fully GPU-resident) needs **2× 96 GB** (+~$8.5k, ~1.2 kW).
- **Bigger frontier-open (DeepSeek-R1 671B):** needs ~450 GB → 512 GB unified-memory box (Mac Studio) or
  8× GPU node. Out of scope for 100 users/day.
- **No self-host (API only):** at 100 users/day, Opus API ≈ **$480–3,000/month** (lean → agent-heavy
  context). Feasible, but Project Y's premise is fully-local → self-host wins on privacy/offline/control.

---

## 10. Cost vs API (context for the decision)
- **Self-host (this box):** ~$9–13k one-time + power; **zero per-token cost**, full privacy/offline.
- **Opus API @ 100 users/day:** ~$0.5–3k/month (no hardware). Break-even vs the box ≈ **6–30 months**
  depending on context size — self-host wins long-term **only because privacy/offline is the requirement**.
- **Recommended hybrid:** serve the bulk on this **local 70B** (free after capex), route the small
  correctness-critical slice to **funded Opus** (~$50–200/month). Best faithfulness-per-dollar.
