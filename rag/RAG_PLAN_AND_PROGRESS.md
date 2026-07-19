# Project Y — Document-RAG: Plan & Progress

_Last updated: 2026-06-25_

Production-grade, fully-local RAG over a corpus of scanned research PDFs, exposed
to Hermes Agent as the `search_docs` MCP tool. This file is the living plan +
status. (Mirror of the approved plan at
`C:\Users\jcvia\.claude\plans\lets-plan-this-out-parallel-meadow.md`.)

---

## Goal & context
- **Corpus:** `rag\pdf\` — **8,823 PDFs / 22 GB**, Philippine agricultural / rubber / ASF research, **mostly English**, **largely scanned (image-only)**.
- **Why:** ground Hermes' answers in these documents (cited, accurate) instead of the local model guessing. The current `rag/` ChromaDB+MiniLM setup was a **prototype** to prove MCP wiring — being replaced by the pipeline below.
- **Guiding principle:** quality is set by **OCR → chunking → retrieval tuning**, not the LLM. For scanned docs, OCR quality is make-or-break (garbage text → garbage retrieval). So: **validate data quality on a sample before scaling**, and roll out **phased**.

## Decisions locked
Phased rollout · **LanceDB** (embedded) · **BGE-M3 + bge-reranker-v2-m3** · Mostly English · GUI progress modal on long jobs.

---

## Tool stack
| Stage | Tool | Role |
|---|---|---|
| Triage | **PyMuPDF (fitz)** | Per-page text-layer detection, bucketing |
| Text extract (TEXT_READY) | **PyMuPDF** | Pull existing text (no OCR) |
| OCR (scanned) | **Marker** (on **Surya 2**) → Markdown | Structured OCR (headings/tables/reading order), GPU |
| OCR sample baseline | **Tesseract** (pytesseract) | Quick CPU read to judge quality |
| OCR alt (tables) | **PaddleOCR PP-StructureV3** | Fallback for table/formula-heavy scans |
| Chunking | **LangChain** `MarkdownHeaderTextSplitter` + `RecursiveCharacterTextSplitter` | Structure-first, token-aware (~512–1024 tok, ~15% overlap) |
| Embedding | **BGE-M3** via **FlagEmbedding** (GPU) | Dense + sparse vectors from one model |
| Vector store | **LanceDB** (embedded) | ANN + full-text (hybrid) + IVF-PQ quantization, no server |
| Reranking | **bge-reranker-v2-m3** | Reorder top-K (biggest quality lever) |
| Query rewrite/HyDE (opt) | Local **Qwen** via llama-server | Expand query before retrieval |
| Orchestration | Python + **SQLite** manifest | Resumable batch over 8.8k files |
| Progress UX | **tkinter** modal (`progress_modal.py`) | Live %/ETA/current-file window on long jobs |
| GPU runtime | **PyTorch + CUDA (cu12x)** in `rag\.venv` | Backs Marker, BGE-M3, reranker |

---

## Staged plan
- **Stage 1 — Triage + OCR-quality gate** (decides everything): classify all PDFs (PyMuPDF), OCR a ~30-file sample, **read it by eye**, pick the production OCR engine on evidence. *Don't scale OCR until this passes.*
- **Stage 2 — TEXT_READY pipeline (quick win):** extract text → chunk → BGE-M3 embed → LanceDB → rewire `search_docs`. Makes the no-OCR bucket searchable + proves the pipeline.
- **Stage 3 — OCR scanned bucket (big batch):** Marker over ~7,077 files, GPU, SQLite-checkpointed/resumable, run with llama-server stopped; feed into the same chunk→embed→index path.
- **Stage 4 — Retrieval quality + faithfulness eval:** hybrid (dense+sparse, RRF) → rerank top 30–50 to top 5 → optional query rewrite. Tune on a hand-built question set. **Includes a RAG-eval harness** (RAGAS / DeepEval / TruLens) over a ~15–20 Q&A set from the corpus to **measure faithfulness, answer-relevancy, context precision/recall** — turns "did it hallucinate/conflate?" into tracked numbers, and quantifies the local-Qwen-vs-Opus gap. (See "Agent integration & faithfulness" findings.)

---

## CURRENT PROGRESS

| Step | Status | Notes |
|---|---|---|
| Prototype RAG (Chroma+MiniLM, `search_docs` MCP wired) | ✅ done | superseded by LanceDB pipeline; proves Hermes wiring works |
| Stage 1a — install `pymupdf` | ✅ done | in `rag\.venv` (1.27.2.3) |
| **Stage 1b — triage all 8,823 PDFs** | ✅ **done** | results below; `triage_output\triage_results.csv` |
| Progress modal (`progress_modal.py`) + wired into triage/sample | ✅ done | tkinter, live %/ETA, headless-safe; unicode crash fixed |
| **Stage 1c — OCR sample + human quality gate** | ✅ **done** | Tesseract baseline + Marker comparison read; **engine = Marker** (see findings) |
| **Stage 2 — TEXT_READY → LanceDB → rewire search_docs** | ✅ **done** | **live**; 52,293 chunks indexed; `search_docs` validated (see below) |
| **Stage 3 — OCR scanned bucket** (Marker) | ✅ **DONE** | 7,077/7,077 OCR'd, 0 errors; 59 dupes skipped; **301,311 OCR chunks** indexed; full corpus **353,604 chunks** live |
| **Stage 4a — hybrid + rerank** (live) | ✅ **done** | dense+FTS RRF + bge-reranker-v2-m3 in `search_docs`; `rag_eval.py` MRR 0.844→0.875, no regressions |
| Stage 4b — query-rewrite/HyDE + RAGAS faithfulness | ⬜ later | optional; best post-OCR on a harder Q set |
| Agent integration — `local-research-rag` skill + MEMORY.md nudges | ✅ done | tool-use + anti-fabrication + anti-conflation; see findings |
| **docs MCP hang fixed** (lancedb-in-worker-thread deadlock) | ✅ done | async tools + main-thread init; see findings |

### Triage results (Stage 1b) — the gate numbers
| Bucket | Count | % |
|---|---|---|
| NEEDS_OCR | 6,971 | 79.0% |
| SUSPECT_TEXT_LAYER | 106 | 1.2% |
| TEXT_READY | 1,741 | 19.7% |
| ERROR_OPEN (corrupt) | 5 | 0.1% |
| **TOTAL** | **8,823** | |

→ **~7,077 files need OCR**, **1,741 can skip it.** Corpus is overwhelmingly scanned.

### Stage 2 results (TEXT_READY pipeline — LIVE) — 2026-06-26
End-to-end built and validated on the 1,741 TEXT_READY files:
- **extract_text.py** → 1,741 docs / **38,588 pages** / 60.5M chars (PyMuPDF, no OCR, 0 errors).
- **chunk.py** → **52,293 chunks**, avg 307 tokens (RecursiveCharacterTextSplitter, BGE-M3 token length, 512/80).
- **embed_index.py** → BGE-M3 dense (1024-d) on GPU → **LanceDB `research`** (52,293 rows) + Tantivy FTS + vector ANN index.
- **rag_mcp_server.py** rewired to LanceDB (query embed on **CPU**, no VRAM). `docs`/`search_docs`/`docs_status` interface unchanged.
- **Validated:** `docs_status` = 52,293 chunks; `search_docs` returns correct, page-cited passages
  (e.g. "microalgae paste feed for milkfish hatchery" → exact UPV-CFOS/DOST-PCAARRD paste papers).
- Gateway restarted; Hermes now answers grounded over the text-ready slice. Stage 3 OCR adds the rest.

**Env gotcha fixed:** Windows w/o Developer Mode can't make HF cache symlinks (`WinError 1314`) → set
`HF_HUB_DISABLE_SYMLINKS=1` in `embed_index.py` + `rag_mcp_server.py`.

**MCP HANG FIXED (2026-06-26) — the `docs_status` 120s timeout.** Symptom: Hermes' `docs_status`/
`search_docs` calls hung ~120s then failed (Hermes blamed "corpus too large" — wrong). **Root cause:
LanceDB's native (Rust) extension DEADLOCKS if it is first imported/initialized in a worker thread
while an asyncio event loop is running.** FastMCP runs tools in its event loop and offloads sync work
to worker threads, so the old server's lazy `import lancedb` / `connect` / `open_table` *inside* a tool
hung every call. Standalone (main thread) it was fine — which is why earlier smoke tests passed.
Diagnosed by bisection: no-op tool=instant; lancedb tool=timeout; even async-API + isolated-worker-loop
still hung; file-logging showed the worker thread never got past `import lancedb`.
**Fix:** in `rag_mcp_server.py`, do `import lancedb` + `connect` + `open_table` AND load BGE-M3 at
**module top (main thread)** before `mcp.run()`; tools are `async def` and only *use* those objects,
running the sync LanceDB/encode calls via `asyncio.to_thread`. Also `TOKENIZERS_PARALLELISM=false`.
Verified over the real stdio MCP transport: init 9.3s (one-time model load), `docs_status` 0.01s,
`search_docs` 1.55s. **Rule: never lazy-import lancedb inside an MCP tool — initialize it in the main
thread at import.**

### Agent integration & faithfulness (2026-06-26)
After the MCP fix, made Hermes actually *use* RAG and *not hallucinate* — both took iteration.

**1) Tool selection (skill + memory nudge).** First real test: asked "how many chunks?" → the local
Qwen agent **ignored `docs_status`** and ran `wc -l stage2/chunks.jsonl` (right number by luck). The
`docs` tools *were* loaded (`hermes mcp test docs` → "Connected 10391ms, 2 tools"; the ~9–10s start is
the BGE-M3 load, within Hermes' init timeout) — it was a **model choice**, not a wiring fault. Fixes:
- Authored skill **`research/local-research-rag/SKILL.md`** (when to call `search_docs`/`docs_status`,
  cite `filename (p.N)`, never grep/wc/find the corpus, honest "no hits").
- Added an always-in-context line to Hermes' **`%LOCALAPPDATA%\hermes\memories\MEMORY.md`**: prefer
  `search_docs`/`docs_status` for any corpus/count question.
- Result: re-test → agent called `mcp_docs_search_docs` and `mcp_docs_docs_status` correctly.

**2) Fabrication (numbers).** Grounded answer still **invented "~$75/L (half the price)"** for local
paste — model halved the real imported "US$150/L". Docs never say $75/L. Fix: **anti-fabrication rule**
in skill + MEMORY.md — *state only figures verbatim in retrieved passages; never compute/convert/infer
numbers; if absent, say "not stated."* Re-test: the $75/L was gone; it correctly used "**half the
market price**" — which IS verbatim in the corpus (`PCAARRD Monitor_Vol1_2016_PCRD-H004490` p.31 &
`no 63-2016_PCRD-H004525` p.7), with accurate citations.

**3) Conflation (topical drift).** Next answer was 8/9 grounded but pulled in *"the Milkfish GAINEX
Project (1997–2001)"* — a **real** project (verified: `PCAARRDMonitor_Q1_2015` p.13) but about milkfish
broodstock/fry, **not** microalgae paste. Real fact, wrong topic. Fix: **anti-conflation clause** in
skill + MEMORY.md — *only include facts the passages tie to the question; don't import related-but-off-
topic items; verify each sentence's claim AND relevance before sending* (lightweight chain-of-verification).

**Verification method (reusable):** scan the LanceDB table directly with FTS + filtered queries
(`tbl.search(q, query_type="fts")`, `tbl.search(dummy).where("stem LIKE '%...%'", prefilter=True)`) to
check each claim against the actual chunk text. NOTE: `to_pandas()/to_lance()` need `pylance` (not
installed) — use FTS/filtered search instead.

**Key takeaways:**
- A skill + MEMORY.md line *steer* a local model strongly but can't *guarantee* tool use or faithfulness
  — Qwen-30B follows them well but not perfectly. Hard guarantee = name the tool, or use **funded Opus**.
- Error classes seen, hardest→easiest to suppress via prompting: invented numbers (now rule-blocked) →
  topical conflation (rule added, partial) → wrong tool (skill+memory mostly fixed).
- **The real lever for faithfulness is model tier** (fund Opus) + clean retrieval (Stage 3 OCR, Stage 4
  rerank); prompt rules are necessary but secondary. This motivates the **Stage 4 RAG-eval harness** to
  measure it instead of eyeballing.

Files touched: `rag\rag_mcp_server.py` (async/main-thread fix), `%LOCALAPPDATA%\hermes\skills\research\
local-research-rag\SKILL.md` (new), `%LOCALAPPDATA%\hermes\memories\MEMORY.md` (nudges). Restart the
gateway (`start-hermes-gateway.ps1`) after skill/memory edits; new CLI sessions reload them on start.

### Faithfulness BASELINE — local Qwen-30B, 17-question set (2026-06-26)
First full run of `RAG_TEST_QUESTIONS.md` against local Qwen (pre rule-sharpening). **~11 PASS / 1
PARTIAL / 3 FAIL.** Per category:
- **Tool use ~12/13 ✅** — called `search_docs`/`docs_status` on nearly everything (skill+memory work).
  Sole miss: **D1** ("keep it strictly about the paste") → skipped the tool, answered from textbook
  knowledge (invented "−20 °C", "10⁸–10⁹ cells/mL"). → rule added: a scoping phrase ≠ skip the search.
- **Abstention 3/3 ✅** — quantum/Bitcoin/ASF all honest; E3 even split ASF *test kit* (in corpus) from
  ASF *vaccine* (not). Strong.
- **Anti-conflation ✅ (D2)** — correctly said GAINEX is "not connected" to microalgae paste.
- **Grounding ✅** — B1 ("2.6 million" spread) and B2 (tilapia ROI/IRR/₱ figures) verified real in corpus.
- **Numeric fabrication ❌ 3 of 4 traps (C1, C2, C4)** — recomputed "half of US$150 = **$75/L**" and stated
  it as fact, despite the rule; **C3 passed** (refused to invent a storage °C). Pattern: the rule holds
  when *abstaining* is the only option, but **fails when a tempting one-step calculation exists**.
  → rule sharpened with the exact worked example ("half the market price" must NOT become "$75/L"; any
  arithmetic → stop and quote verbatim). **RE-TEST after sharpening: C1 flipped FAIL→PASS** — Qwen now
  answers *"USD price not explicitly stated; local is ₱1,500/L, commercial US$50–150/L"*, no "$75/L", and
  it even retrieved the US$50–150/L range it had missed in C4. (One passing run ≠ guaranteed reliability
  on a stochastic local model, but the worked-example framing clearly moved the needle.) **C2 and D1 also
  flipped FAIL→PASS on re-test:** C2 refused the "$75 cheaper" computation ("no arithmetic applied"); D1
  *called the tool* and gave grounded corpus facts (flocculant harvesting, UPV Miag-ao) instead of the
  textbook −20 °C/cell-density junk. **Net: all 3 baseline FAILs → PASS after rule-sharpening** (still
  single stochastic runs — Stage-4 harness will measure pass-RATE). Recurring minor: cites by result-
  number "[3]" instead of `filename (p.N)` → added a citation-format line to the skill.
**Conclusion (the thesis, now measured):** prompt rules **solve integration + abstention** on local
Qwen but **cannot reliably stop numeric fabrication** (1/3 on the number trap). The real fix is **model
tier (funded Opus) + clean retrieval (Stage 3 OCR, Stage 4 hybrid+rerank)**. This row is the **baseline**
to beat — re-run the same 17 Q on (a) Qwen post-sharpening, (b) funded Opus, (c) post-Stage-4 retrieval;
the **Stage 4 RAG-eval harness** turns this into tracked RAGAS/DeepEval scores. (Retrieval-recall gap also
seen: C4 missed the real "US$50–150/L" range — it's in the corpus, just not in the chunks retrieved that
turn → exactly what hybrid+rerank targets.)

### Data-quality finding — "TEXT_READY" is not uniformly clean (2026-06-26)
Some PDFs were **scanned and badly OCR'd by whoever produced them**, so they carry a garbage *embedded
text layer*. Triage classifies on text **presence** (chars/page), not **quality**, so these land in
TEXT_READY and their garbled text got into the Stage 2 index (no quality filter applied yet).
- **Example flagged by user:** `2018-03-14-03_ab_56299` (a thesis) — pp.1–8 are glyph soup
  (`Ci' H't'(t.\\L L\\JZO!J`); only the abstract p.9 is partly usable. Clean-word ratio 0.43.
- **Prevalence (clean-word-ratio scan over all 1,741):** median **0.73** (healthy). Bad tail is small:
  **~18 docs (1.0%) < 0.30** (near-pure noise), **25 (1.4%) < 0.40**, **39 (2.2%) < 0.50**.
  Worst (ratio ~0.00): `NP-00033_PCRD-H009168`, `VF-02048_PCRD-H009676`, `NP-00035_PCRD-H009170`,
  `Production and characterization of fuel pellets_ab_250211`, `FS-01483_PCRD-H002582`,
  `Marker assisted selection_PCRD-H000079`, `Spatial variability of phytoplankton_PCRD-H000095`,
  `NP-00034/36/37_*`, plus `PZglIfVHJaJutHaKpvxxcOJ4b9JxGBwcH0FJA7pU`.
- **Impact now:** low — garbled chunks rarely match clean query embeddings, so they seldom surface; but
  they're cruft in the 52,293-chunk index.
- **Planned mitigation (deferred — when we resume):**
  1. Add a **clean-word-ratio quality filter** to `chunk.py` (drop chunks below ~0.4–0.5) — the same
     filter also cleans Stage 3 OCR output; then **re-chunk + re-embed** Stage 2 (cheap, ~minutes).
  2. **Re-route** the worst bad-text-layer files into the **Stage 3 Marker OCR** set — OCR the source
     images instead of trusting the broken layer (Marker will likely recover them). They're effectively
     scanned docs mislabeled TEXT_READY.
- **Not done now** — index is left as-is while Hermes uses the text-ready slice; revisit with Stage 3.

---

## Files in `rag\`
**Active / new (this build):**
- `pdf\` — the 8,823-PDF corpus (22 GB)
- `pdf_triage_and_sample.py` — Stage 1 triage + OCR sampling (now with progress modal)
- `progress_modal.py` — reusable tkinter progress window
- `triage_output\triage_results.csv` — per-file triage classification

**Prototype (superseded by LanceDB pipeline — keep or delete later):**
- `index_docs.py`, `rag_mcp_server.py` (← will be **rewired** to LanceDB, same `search_docs` interface), `chroma\`, `docs\`, `README.md`
- `.venv\` — shared venv (chromadb, mcp, pypdf, pymupdf; will add marker/FlagEmbedding/lancedb/torch)

**Planned (Stages 2–3):**
- `extract_text.py`, `chunk.py`, `embed_index.py`, `ocr_run.py`, `lancedb\`, `ocr_out\`, SQLite manifest

---

## Stage 1c in detail — the OCR-quality gate (how it works)

**Purpose.** OCR-ing the ~7,077 scanned files is the expensive part (multi-hour/overnight GPU
batch). If OCR comes out garbled, *everything* downstream is poisoned — bad text → bad
embeddings → bad retrieval — and you'd only find out after burning hours. Stage 1c OCRs a
**tiny representative sample first** so you can **read the output and judge quality before
committing**, and so you pick the right OCR engine on evidence. Highest-value 5 minutes in
the project.

**Step 1 — What gets installed (and the gotcha).**
- **Tesseract OCR engine** (`tesseract.exe` system binary, via winget → `C:\Program Files\Tesseract-OCR\`) — the **quick CPU baseline**, easiest to stand up.
- **`pytesseract` + `pillow`** into `rag\.venv` (Python wrapper + image handling).
- **Gotcha:** `pytesseract` must *find* `tesseract.exe`. After a winget install it's often not on
  the current session's PATH → either add it to PATH or set
  `pytesseract.pytesseract.tesseract_cmd` to the full path. Verify it resolves before running.
- Tesseract is **CPU** → no VRAM contention; safe to run with llama-server up.

**Step 2 — How the 30 files are chosen.** `sample` reads `triage_output\triage_results.csv`,
filters to the **`NEEDS_OCR`** bucket (6,971 files), and selects an **even spread** across that
list (`step = len(rows)/n; rows[int(i*step)]`) — *not* the first 30. The corpus spans many
sources/folders/doc types; an even spread makes the verdict generalize to the whole scanned set.

**Step 3 — What it does per file (read-only on the PDFs).**
1. Open the PDF with PyMuPDF.
2. Render up to **`--max-pages` (default 3)** pages to images at **`--dpi` (default 200)** —
   `page.get_pixmap(dpi=...)`. Higher DPI = sharper = better OCR but slower.
3. Run **Tesseract** on each rendered page → text.
4. Write `triage_output\ocr_samples\NNN__<filename>.txt` (pages split by `----- page N -----`).
5. Record `sample_manifest.json` (source → output, char counts).
Nothing in `rag\pdf\` is modified — only renders in memory + writes text dumps to `triage_output\`.

**Step 4 — The modal.** `ProgressModal` pops up ("OCR Sampling") with bar + live %/ETA/current
filename. Defaults (30 files × 3 pages, Tesseract CPU) → **~2–5 min**. Run it in **your own
terminal** so the GUI window surfaces (a background launch falls back to console prints).

**Step 5 — The gate: you read the output.** Open ~10 of `triage_output\ocr_samples\*.txt` and judge:
- **Words/sentences intact**, or garbled character soup?
- **Multi-column** read in order, or interleaved into nonsense?
- **Tables** survived as readable rows, or scrambled?
- **Domain specifics correct** — cultivar names, ASF strain codes, measurements/units, numbers?
- Bottom line: **is enough meaning preserved that an embedding would retrieve it correctly?**
The script reports *how many* characters; only you can tell if they're *meaningful*.

**Step 6 — The decision it produces.**
- **Tesseract clean** → viable (cheap, CPU). Unlikely for scanned research with tables.
- **Rough (expected)** → escalate on evidence: switch engine to **Marker (Surya-based, structured
  markdown, GPU)** and compare the same 30 files; and/or raise `--dpi` to 300; and/or preprocess
  (deskew/denoise) poor scans. Re-sample and re-read.

**Caveat — Tesseract vs Marker.** This script samples with **Tesseract** (PaddleOCR path exists;
docTR is a stub). **Marker is *not* wired into this script** (it works whole-document, not
per-page pixmaps). So: this gives the **Tesseract baseline**; a **Marker comparison is a separate
small run** on the same 30 files. Given 80% scanned research with tables, Marker is the expected winner.

**Notes.** The sample pulls only from `NEEDS_OCR`; the 106 `SUSPECT_TEXT_LAYER` files (partial
text layers) are a separate, smaller look later. This gate **must pass before** the multi-hour
Stage 3 OCR run.

---

## Stage 1c findings — Tesseract baseline read (2026-06-25)

Read the 30 dumps in `triage_output\ocr_samples\`. Clean typed **body prose OCRs well**
(e.g. `004`, `030` main text, the biographical sketch in `002` page 4). Two distinct error
classes stood out — and they need **opposite** responses:

**1) Handwritten signatures / dates — IGNORE (not an engine problem).**
On thesis approval pages, cursive signature ink sitting over/next to the *printed* committee
names corrupts those names. Example — `002__54706_ab_54706.txt` p.3:
`ELPIDIO L. ROSARIO` → `Ay L. ROSARIO`; `RAFAEL P. CREENCIA` → `D, CREENCIA`; handwritten dates →
`avd 11490 Hacroe 3, 19f?`.
- **Cause:** handwriting. **No** OCR engine (Tesseract / Marker / Paddle) reads cursive reliably.
- **Impact:** negligible — confined to **front-matter boilerplate** with ~zero retrieval value;
  the real author/title is on the typed title page (`002` p.2 OCR'd flawlessly).
- **Decision:** do **not** special-case signature removal (fragile). Leave it; a **generic
  low-quality-chunk filter in Stage 2** (drop chunks that are mostly gibberish/non-dictionary
  tokens) sweeps this up corpus-wide.

**2) Caption/body interleaving — FIXABLE, and the deciding weakness.**
A figure caption got spliced into the **middle of a body sentence**. Example —
`030__VF-01977_PCRD-H009603.txt` lines 18–22: the sentence "Microalgae are *microscopic floating
aquatic organisms that are usually found in marine and freshwater environments*" has
"`Frozen microalgae paste (Image credit: Inland … Research Division (IARRD),`" merged into it.
- **Cause:** Tesseract has **no layout/region model** — it reads the page straight across, so a
  caption box embedded between body lines gets merged into the prose (reading-order failure).
- **Impact:** **higher** — corrupts *real content*, not boilerplate, and recurs anywhere there are
  figures / captions / columns (common in research docs). A gibberish filter **won't** catch it:
  it's clean words in the wrong place.
- **Decision:** this is exactly what **structured OCR (Marker / Surya region detection)** fixes →
  run the Marker comparison on the same 30 files. **Strongest evidence so far for Marker.**

**Also noted:** stylized/decorative **title pages** mangle (`002` p.1 → `GROWTH OF YOUNG AOZUSTA
CORFE`) while the plain restated title (p.2) is perfect. Pattern: **Tesseract is solid on clean
typed body prose, weak on decorative / handwritten / overlapping / multi-region layout.**

**Verdict:** body-text quality is fine; **layout handling is the gating weakness** → proceed to the
**Marker comparison** (same 30 files) before locking the Stage 3 production engine.

### Marker (Surya) comparison — single-file proof (file 030, page 1)
Ran Marker on the exact caption-splice page. **It fixed the layout failure outright:**

| | Tesseract baseline | Marker (Surya) |
|---|---|---|
| Caption vs body | caption spliced **into** the body sentence | caption is a **separate block**; body sentence **intact** |
| Caption text | `SeRARROR ces Research Division` | recovered `Inland **Aquatic Resources** Research Division (IARRD), PCAARRD` |
| Structure | flat text | Markdown headings (`##`), species *italicized*, image placeholders kept |
| Symbols | dropped/garbled | `₱18,000` / `₱22,500` correct |

Marker output is also **Markdown** → feeds the structure-first chunker directly. **Engine decision: Marker
is the production OCR backend** (pending the user's read of the full 30-file comparison to confirm it holds
across the spread). Perf: ~14 s/page with models cached (one-time Surya weight download ~5 min, ~1.35 GB+).

**Env locked for Marker:** `marker-pdf 1.10.2`, `surya-ocr 0.17.1`, **`torch 2.11.0+cu128` (CUDA on RTX
4080 SUPER, `cuda.is_available()=True`)**. Note: PyPI's `torch==2.12.1` is **CPU-only**; the CUDA build
had to come from `--index-url https://download.pytorch.org/whl/cu128` (which tops out at 2.11.0+cu128).

### Full 30-file Marker read — residual issues (all non-blocking)
Reviewed all 30 `marker_samples\*.md`. Marker is the clear winner; the remaining defects cluster into
four classes, none of which block the build:

| # | Example | Symptom | Class | Action |
|---|---|---|---|---|
| 1 | (comparison setup) | only first 1–4 pages OCR'd | **my page cap** (matched the Tesseract sample for fairness) | removed in Stage 3 — full docs |
| 2 | `004` feedback form; `006` "PROGRAMME" banner | form grid → garbage table; letter-spaced heading → fake table row | **hard-for-all-OCR boilerplate / decorative type** | quality filter drops it; real content on same pages is clean; optional LLM mode |
| 3 | `014` "List of Research Paper Series" table | structure correct, **characters** garbled (`Optobor 1978`, Georgian glyphs) | **source scan quality** (faint/low-res) — a physical ceiling no engine beats | higher render DPI + denoise help faint text; table structure survives → accept |
| 4 | `029` Tan Awards newsletter | paragraphs after figures out of order | **reading-order on complex multi-column+figure layout** | semantic chunking tolerates (chunks embed independently); minority of corpus |

**Tunable noted — Marker LLM mode (`use_llm=True`):** improves reading order, cross-page table merges,
and decorative headings by calling an LLM. Can point at the **local Qwen** (llama-server). Much slower
per page → **not** for the full 7k batch; keep as a **targeted re-process** for docs where order matters.

**Decision:** **Marker locked as the production OCR engine.** Residuals are boilerplate (filtered),
source-quality ceilings (unrecoverable), or layout edge cases (RAG-tolerable).

### Stage 3 sizing — REAL workload (from `pages_total` in the triage CSV)
The abstract-heavy 30-file sample (1–4 pp) **badly understated** the corpus. Actual OCR workload:

| Bucket | Files | Pages |
|---|---:|---:|
| NEEDS_OCR | 6,971 | 174,152 |
| SUSPECT_TEXT_LAYER | 106 | 2,593 |
| **OCR total** | **7,077** | **176,745** |
| TEXT_READY (no OCR) | 1,741 | 39,305 |

**Avg 25 pages/file.** Heavy tail dominates: **425 files have 101+ pages = 82,766 pages (47% of all OCR
work)** — full theses/proceedings/books. Distribution of OCR-bound files: 1pp ×486 · 2–4pp ×1,500 ·
5–10pp ×1,610 · 11–20pp ×1,970 · 21–50pp ×747 · 51–100pp ×339 · 101+pp ×425.

**Time estimate — BENCHMARKED (2026-06-27, on the 4080 Super):**
- Steady-state on a **100-page** doc: **0.45 pages/s (2.2 s/page)** → full 176,745 pages ≈ **~4.6–5 days serial**.
- A 20-page doc ran 3.9 s/page; **1-page docs ~9–32 s each** → **small docs are slower per-page** (fixed
  per-doc overhead: PDF load + pipeline setup + render). So the small/medium tiers run slower than a naive
  pages×rate estimate; total is realistically **~5 days**.
- **Batch-size speedup did NOT help:** bumping Surya `*_BATCH_SIZE` env vars gave 0.43 vs 0.45 pg/s, peak
  VRAM only ~7.1 GB. Bottleneck is the sequential pipeline / CPU stages, not GPU batch size → no free
  speedup on this hardware. (`marker-pdf` doesn't saturate the 16 GB.)
- **Runner validated:** `ocr_run.py` seeds 7,077 files into a SQLite manifest, OCRs to `ocr_out/<stem>.md`,
  tracks status, resumes (tested 3 files: 3 ok / 0 err; `done 3 / pending 7,074`).
- **Faster paths if ~5 days on the 4080 is too long:** (a) the planned **Llama-70B server's Ada/Blackwell
  GPU** would do this ~2–4× faster AND keep Hermes online on the 4080; (b) a **cloud A100/H100 burst**
  (~$30–50) finishes in ~12–24 h.
- **Decision (user):** run the **full batch on the 4080** now (`ocr_run.py --tier all`), resumable across
  nights; Hermes offline during runs.

### Stage 3c — OCR → index pipeline BUILT & VALIDATED (2026-06-27)
The whole "OCR markdown → searchable" path is built and tested end-to-end on 4 real OCR'd docs:
- **`ocr_run.py`** now sets `paginate_output=True` → Marker inserts page separators
  `\n\n{PAGE_ID}` + ("-"×48) + `\n\n` (PAGE_ID 0-indexed) so chunks get **page-accurate citations**.
- **`chunk.py --source ocr`** parses those separators (regex `\{(\d+)\}-{20,}`), chunks each page with
  **markdown-aware** separators (BGE-M3 token length), and applies a **garble filter** (drops chunks
  whose 3+-letter tokens are mostly vowel-less consonant-soup; keeps number/table chunks) → `stage3/chunks_ocr.jsonl`.
- **`embed_index.py --chunks stage3/chunks_ocr.jsonl --mode append --dedupe-ocr`** embeds + **appends** to
  the live `research` table (idempotent: `--dedupe-ocr` first deletes rows `source LIKE '%ocr_out%'`).
- **Validated:** 4 docs → 21 chunks (pages 1–11 correctly attributed, 0 garble-dropped) → appended
  (52,293 → 52,314 rows) → the OCR doc is retrievable.

### Stage 3 COMPLETE — full corpus LIVE (2026-07-08)
OCR batch finished: **7,077/7,077 files, 176,745 pages, 0 errors** (ran ~5 days on the 4080 via the
logon-Startup auto-resume). Indexed via `dedupe_ocr.py -> chunk.py --source ocr -> embed_index.py
--mode append --dedupe-ocr`:
- Dedup excluded **59 duplicate .md** (52 sets); chunked **7,018 docs -> 301,311 chunks** (avg 286 tok,
  64 garble-dropped, page-attributed).
- LanceDB table `research`: **353,604 rows** = 52,293 TEXT_READY + 301,311 OCR (exact-count verified).
- **Validated** on scanned-ONLY docs: "Holothuria scabra ocean nursery tray" -> *Viability of a bottom-set
  tray ocean nursery system* p.3 (rerank 0.98); goat-production + PhilRubber-PRIME queries also hit their
  scanned sources at 0.95-0.99. Page citations correct. Warm latency ~1.5 s/query (exact over 354k + rerank).
- Startup auto-resume `.vbs` retired; llama-server + gateway restarted. **The full 8,823-doc / 22 GB corpus
  is now searchable through hybrid+reranked `search_docs`.**

**MAJOR retrieval fix found here — dropped the IVF-PQ vector index; search is now EXACT.** With the
ANN index, a sparse match (a 2-chunk OCR doc) was **missed entirely** at default `nprobes`, and PQ
quantization **degraded scores** (it scored 0.23 and ranked nowhere). After `tbl.drop_index('vector_idx')`,
**exact (brute-force) search**: the doc ranks **#1 at score 0.65 in ~78 ms**. At 52k–250k chunks exact
search gives 100% recall + full-precision scores for negligible latency, so `embed_index.py` **no longer
builds the IVF-PQ index** (revisit past ~1M chunks). `rag_mcp_server.py` needs no change (its plain
`.search().limit()` is now exact). *This also means Stage 2's earlier scores (~0.38) were PQ-degraded —
real relevance is higher.*

### Stage 4a — hybrid + rerank LIVE (2026-06-27)
`search_docs` is now **hybrid + reranked**: dense (exact vector) + FTS/BM25, top 30 each → **RRF fusion**
→ **bge-reranker-v2-m3** cross-encoder reranks ≤40 candidates → top k. Reranker runs on **CPU** (coexists
with llama-server); graceful **fallback to dense-only** if it fails to load. Server startup ~12.6 s
(BGE-M3 + reranker, main-thread load) — `hermes mcp test docs` connects fine (2 tools); gateway live.
- **`rag_eval.py`** (judge-free hit@1 / hit@5 / MRR vs dense-only) on 8 verified queries:
  **hit@1 6/8 (both) · hit@5 8/8 (both) · MRR 0.844 (dense) → 0.875 (hybrid+rerank)**. Discriminating
  case: *"which microalgae species…"* dense rank **4 → 2**. **No regressions.**
- Gains are **modest on this easy/clean TEXT_READY set** (dense is already strong after the exact-search
  fix); rerank's value compounds on the **larger/noisier full OCR corpus** and harder queries. Latency
  ~1.5–2 s/query (reranker on CPU) — fine for RAG (LLM generation dominates).
- **Remaining Stage 4 (later):** optional **query-rewrite/HyDE** (local Qwen before retrieval); **RAGAS/
  DeepEval faithfulness** harness (needs an LLM judge) — best run post-OCR on a harder question set.
- **Re-run `rag_eval.py` after the full OCR index** to measure the real hybrid+rerank lift on the
  complete corpus (this is the baseline to beat).

### Stage 4d — full-corpus retrieval eval (2026-07-08)
Expanded `rag_eval.py` to **15 queries** (7 text-ready + 8 newly-OCR'd scanned; ground truth from
independent FTS/keyword search, case-insensitive stem match) and ran on the **353,604-row** table:

| metric | dense-only | hybrid+rerank |
|---|---|---|
| hit@1 | 8/15 | 9/15 |
| hit@5 | **13/15** | 12/15 |
| MRR | 0.660 | **0.678** |

**Honest read:** hybrid+rerank is a **near-wash vs dense** here (+1 hit@1, −1 hit@5, +0.018 MRR) — it
helps some (which-microalgae 6→3, PhilRubber-PRIME 3→1, Comperiella 2→1) and hurts others (native-chicken
5→>10, HPSD 1→3). Retrieval is **harder at scale**: MRR fell from ~0.84–0.88 on the clean 52k slice to
~0.66–0.68 on the 354k corpus (7× more competing chunks) — expected. **Deepening candidates 30/40→60/80
changed NOTHING** (byte-identical ranks, just slower) → reverted to 30/40; candidate depth is not the lever.
- **Two genuine gaps:** "tilapia profitability" (>10 both — the profitability-table chunk doesn't rank for
  the NL query at scale) and native-chicken-under-hybrid. These are **ranking/vocab-mismatch** problems →
  the real fix is **query-rewrite/HyDE** (expand "profitability" → ROI/IRR/break-even terms) and/or better
  table chunking, NOT more candidates.
- **Caveat:** single-ground-truth eval **understates** real quality — the reranker often surfaces a
  *different but still relevant* doc that the eval scores as a "miss."
- **Decision:** keep hybrid+rerank (marginal net win + far better relevance *scores* — 0.98 vs 0.22
  discrimination — which the agent uses), at ~2 s/query. Bigger retrieval gains now require HyDE/chunking,
  not rerank tuning.

### HyDE — built as opt-in `search_docs(hyde=True)` (2026-07-08)
Wired HyDE into `rag_mcp_server.py`: `_hyde_sync()` asks the local LLM (`:8080/v1`) for a hypothetical
answer passage; the DENSE side embeds `query + hypothetical` (FTS + rerank still on the raw query, EXCEPT
in HyDE mode the rerank also uses the enriched text — a numeric table chunk reranks better against
vocab-rich text). Graceful fallback to plain query on any LLM error/timeout. Default OFF (fast search
unchanged); `hyde=True` is a **fallback** for factual/table queries.
- **Proven mechanism:** for "tilapia profitability" (missed by plain, dense-rank >15), HyDE gets the
  H003804 profitability-table chunk to **dense-rank 1**; diagnosis showed the pipeline then loses it
  (RRF splits it across chunk-IDs → cand-rank 8; raw-query rerank → 12; **rerank-on-HyDE-text → 5**).
- **Honest limitation on THIS stack:** the local **Qwen3-Thinking** model is (a) **slow** — ~25 s/call
  (burns tokens reasoning; `/no_think` ignored), and (b) **non-deterministic even at temp 0** (llama.cpp
  MoE + FA/quantized-KV → float nondeterminism), so HyDE lands the hard target at ~**rank 4 about half the
  time**, not reliably. Net: a real-but-flaky fallback, not a default.
- **Where it becomes good:** on the planned **Llama-3.3-70B server** (non-thinking, deterministic, ~2 s
  generation) HyDE would be fast + stable and could be the default — the current stack is the bottleneck,
  not the technique.
- Skill updated: retry factual/numeric misses with `hyde=True`.

**Stage 3 plan revision (driven by the above):**
1. **Do Stage 2 first** (TEXT_READY, 39,305 pages, **no OCR**) — real searchable value while OCR strategy firms up.
2. **Switch Stage 3 to Marker's batch path**, not the per-file Python loop. Benchmark real pages/sec on a
   20-page and a 100-page doc to get a defensible number.
3. **Tier the OCR rollout:** small/medium docs first (1–20 pp ≈ 4,100 files / ~46k pages) for breadth,
   then the 425 giant docs (~83k pages) as a separate long resumable batch (overnight, llama-server stopped).
4. Keep it **resumable** (SQLite manifest) — at ~days of runtime, crash-safety is mandatory.

---

## Command reference (every stage)

> Conventions: project root = `C:\Users\jcvia\PyCharmMiscProject\ProjectY`; RAG venv python =
> `rag\.venv\Scripts\python.exe`; uv = `C:\Users\jcvia\AppData\Local\hermes\bin\uv.exe`. Run RAG
> commands from the `rag\` folder. **Run long/GUI jobs in your own terminal** so the modal shows.

### Stage 1a — venv + triage deps (DONE)
```powershell
cd C:\Users\jcvia\PyCharmMiscProject\ProjectY\rag
# create the isolated RAG venv (once)
& "C:\Users\jcvia\AppData\Local\hermes\bin\uv.exe" venv .venv
# Stage-1 dependency
.\.venv\Scripts\python.exe -m pip install pymupdf
```

### Stage 1b — triage all 8,823 PDFs (DONE)
```powershell
cd C:\Users\jcvia\PyCharmMiscProject\ProjectY\rag
.\.venv\Scripts\python.exe .\pdf_triage_and_sample.py triage rag\pdf
# → triage_output\triage_results.csv  (buckets: TEXT_READY / NEEDS_OCR / SUSPECT_TEXT_LAYER / ERROR_*)
```

### Stage 1c — OCR sample + human gate (NEXT)
```powershell
# 1) Install Tesseract engine (system binary)
winget install --id UB-Mannheim.TesseractOCR -e
#    (if not auto-added) make pytesseract find it for THIS session:
$env:Path += ";C:\Program Files\Tesseract-OCR"
tesseract --version          # verify the engine resolves

# 2) Python wrappers into the RAG venv
cd C:\Users\jcvia\PyCharmMiscProject\ProjectY\rag
.\.venv\Scripts\python.exe -m pip install pytesseract pillow

# 3) Run the sample IN YOUR OWN TERMINAL (modal shows; reads NEEDS_OCR, even spread)
.\.venv\Scripts\python.exe .\pdf_triage_and_sample.py sample --n 30
#    quality knobs:  --n 30 (files)   --dpi 200 quick / 300 sharper   --max-pages 3
#    sharper re-run example:
.\.venv\Scripts\python.exe .\pdf_triage_and_sample.py sample --n 30 --dpi 300 --max-pages 4

# 4) Read the dumps (gate) — open ~10 by eye
explorer .\triage_output\ocr_samples
```
To switch the sampler's engine later: set `OCR_BACKEND` near the top of
`pdf_triage_and_sample.py` (e.g. `"paddleocr"`) and install that engine.

**Marker comparison (same 30 files) — `marker_sample.py`:**
```powershell
# deps (already installed): marker-pdf + CUDA torch
& "C:\Users\jcvia\AppData\Local\hermes\bin\uv.exe" pip install --python .\.venv\Scripts\python.exe marker-pdf
& "C:\Users\jcvia\AppData\Local\hermes\bin\uv.exe" pip install --python .\.venv\Scripts\python.exe `
    --reinstall-package torch torch --index-url https://download.pytorch.org/whl/cu128

# free the GPU first (Marker uses VRAM)
Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force

# run in YOUR terminal (modal shows); reads ocr_samples\sample_manifest.json, same 30 files
cd C:\Users\jcvia\PyCharmMiscProject\ProjectY\rag
.\.venv\Scripts\python.exe .\marker_sample.py        # writes triage_output\marker_samples\*.md
explorer .\triage_output\marker_samples               # compare vs ocr_samples\*.txt

# restart serving model when done
powershell -File C:\Users\jcvia\PyCharmMiscProject\ProjectY\start-llama-server.ps1
```

### Stage 2 — TEXT_READY pipeline → LanceDB → rewire search_docs (planned)
```powershell
cd C:\Users\jcvia\PyCharmMiscProject\ProjectY\rag
# deps
.\.venv\Scripts\python.exe -m pip install FlagEmbedding lancedb langchain-text-splitters
# PyTorch + CUDA (cu12x) for GPU embedding
.\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu124

# pipeline (TEXT_READY bucket only)
.\.venv\Scripts\python.exe .\extract_text.py          # PyMuPDF text → staged text
.\.venv\Scripts\python.exe .\chunk.py                 # Markdown+Recursive splitter, ~512-1024 tok, 15% overlap
.\.venv\Scripts\python.exe .\embed_index.py           # BGE-M3 (GPU) → LanceDB (rag\lancedb\) + FTS index

# rewire the MCP server backend to LanceDB (same search_docs interface), then restart gateway:
powershell -File C:\Users\jcvia\PyCharmMiscProject\ProjectY\start-hermes-gateway.ps1
```

### Stage 3 — OCR the scanned bucket (Marker, resumable; planned)
```powershell
# Marker (pulls surya + torch/CUDA)
cd C:\Users\jcvia\PyCharmMiscProject\ProjectY\rag
.\.venv\Scripts\python.exe -m pip install marker-pdf

# FREE the GPU first: stop llama-server (frees full 16 GB for OCR throughput)
Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force

# resumable batch OCR (SQLite manifest pending/done/error) → rag\ocr_out\<stem>.md (paginated)
.\.venv\Scripts\python.exe .\ocr_run.py --tier all    # small docs first → giants last; Ctrl-C-safe, re-run to resume
#   tiers: --tier small (1-10pp) | medium (11-50pp) | large (51+pp) | all ;  --status to check progress

# when OCR done (or incrementally): DEDUP → chunk OCR markdown → embed → APPEND (idempotent)
.\.venv\Scripts\python.exe .\dedupe_ocr.py                               # refresh dup skip-list (52 sets / 59 redundant copies)
.\.venv\Scripts\python.exe .\chunk.py --source ocr                       # → stage3\chunks_ocr.jsonl (excludes dupes; page-attributed + garble filter)
.\.venv\Scripts\python.exe .\embed_index.py --chunks stage3/chunks_ocr.jsonl --mode append --dedupe-ocr

# restart serving model when the batch is done
powershell -File C:\Users\jcvia\PyCharmMiscProject\ProjectY\start-llama-server.ps1
```

### Stage 4 — hybrid + rerank + query rewrite (planned)
```powershell
cd C:\Users\jcvia\PyCharmMiscProject\ProjectY\rag
# reranker model is bge-reranker-v2-m3 (via FlagEmbedding, already installed in Stage 2)
# tuning happens in rag_mcp_server.py / retrieval config; evaluate on a hand-built question set:
.\.venv\Scripts\python.exe .\rag_eval.py --k 5 --topn 10   # hit@1/hit@5/MRR: dense vs hybrid+rerank (judge-free)

# RAG-eval harness (faithfulness / answer-relevancy / context precision-recall):
& "C:\Users\jcvia\AppData\Local\hermes\bin\uv.exe" pip install --python .\.venv\Scripts\python.exe ragas deepeval
#   build qa_eval.jsonl (~15-20 {question, ground_truth} from the corpus), then score:
.\.venv\Scripts\python.exe .\rag_eval.py              # RAGAS: faithfulness, answer_relevancy, context_*
#   note: RAGAS/DeepEval use an LLM judge — point at funded Opus or a capable model for trustworthy scores

# after retrieval changes, reload the running server:
powershell -File C:\Users\jcvia\PyCharmMiscProject\ProjectY\start-hermes-gateway.ps1
```

### Service control (used throughout)
```powershell
# start serving model (detached)
powershell -File C:\Users\jcvia\PyCharmMiscProject\ProjectY\start-llama-server.ps1
# stop serving model (free VRAM for batches)
Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
# start / restart Hermes gateway (after any MCP backend change)
powershell -File C:\Users\jcvia\PyCharmMiscProject\ProjectY\start-hermes-gateway.ps1
# quick health check
Invoke-RestMethod http://127.0.0.1:8080/health      # llama-server up?
```

> **Decide OCR engine** on the Stage 1c evidence (expected: Marker beats Tesseract on these scans)
> → then size + run Stage 3.

## Ops notes
- **GPU contention:** serving Qwen llama-server uses ~13.5/16 GB. Run heavy GPU batches (OCR, embedding) with **llama-server stopped**; at query time run embed+rerank on **CPU** so they coexist with the serving model.
- **Run long jobs in your own terminal** to see the progress modal (background launches may not surface the GUI; they fall back to console).
- **Hermes integration:** only `rag_mcp_server.py`'s backend changes; the `search_docs` MCP contract stays, so no Hermes reconfig — just restart the gateway after rewiring.

## Decision gates
1. After triage → scope/time (DONE: 80% need OCR).
2. After OCR sample (human read) → pass/fail + engine choice. ← **we are here**
3. After Stage 2 quick win → confirm retrieval quality before the big OCR batch.

## Verification (per stage)
- S1: `triage_results.csv` populated; read ≥10 sample `.txt` dumps.
- S2: `search_docs` returns correct passage + citation from a known TEXT_READY paper.
- S3: a fact existing **only** in a scanned doc is retrievable post-OCR.
- S4: on ~15–20 hand questions, hybrid+rerank puts the right doc in top-5 more often than dense-only; Hermes answers cited + grounded (CLI + Discord).

---

## Stage 5 — Agent tool-calling reliability (2026-07-08)
**Problem:** Hermes often failed to actually *use* `search_docs` for corpus questions — it grepped,
looped, or answered from the web. Root cause was NOT skill/memory wording (those are fine). Three
compounding issues, isolated with headless `hermes chat -q ... -v` runs + direct llama-server probes:

1. **Trap tools.** For "search my research" both Qwen variants reach for `search_files` (built-in
   grep) / `mcp_filesystem_search_files` over the real `mcp_docs_search_docs`. (Instruct did this 3/3.)
2. **Thinking model derails.** Qwen3-30B-A3B-**Thinking** reasons right but is slow (~113 s/call) and
   in the agent loop looped `search_files` 145× to the 150-turn budget. Instruct picks in ~1.5 s.
3. **Cold-start race.** The docs MCP loaded BGE-M3 (~12 s) *before* answering the MCP handshake, so
   `hermes chat` finalized its toolset **without** `mcp_docs_search_docs` → the agent wandered.
   (The warm gateway avoids this; fresh CLI sessions hit it.)

Also found: config lists **Opus as primary but it's unfunded** (400 credit-balance) → every query
silently falls back through OpenAI (429) to **local Qwen**. So the loaded local model is what runs.

**Fix (all local/free), commands used:**
```powershell
# 1. Serve the Instruct variant (edit start-llama-server.ps1: swap $model to
#    Qwen3-30B-A3B-Instruct-2507; add '--temp','0.3' — see Stage 7, NOT greedy 0), then restart:
taskkill /IM llama-server.exe /F ;  .\start-llama-server.ps1

# 2. Lean toolset — drop the search_files traps (both platforms):
hermes tools disable file terminal --platform cli
hermes tools disable file terminal --platform discord
#    + config.yaml: mcp_servers.filesystem.enabled: false   (removes mcp_filesystem_search_files)

# 3. docs MCP loads BGE-M3+reranker at MODULE TOP / main thread (~12 s registration).
#    NOTE: a background-thread "fast registration" variant was tried and REVERTED — it HANGS the
#    reranker init under FastMCP's asyncio loop (searches time out). See Stage 7. lancedb stays main-thread.
hermes mcp test docs        # verify: Connected ~12s, 2 tools

# 4. Apply to Discord:
hermes gateway stop ;  .\start-hermes-gateway.ps1
```

**Validated end-to-end:** `hermes chat -q "...at what age do rubber trees start latex tapping?"` →
called `mcp_docs_search_docs`, retrieved real passages (Duke 1989; `FB-00963_…H009033` p.55),
answered **"5–8 years old / 45–50 cm trunk diameter"** grounded + cited, ended naturally
(`tool_turns=2`, `finish_reason=stop`, no loop). Cold CLI ~3.5 min (CPU rerank + one-time model
load dominate); warm gateway is faster.

**Tradeoffs to remember:** Instruct loses the Thinking variant's deep multi-step reasoning; the lean
toolset means Hermes has no local filesystem/shell tools (by design — Claude Code covers that). Both
are reversible (uncomment Thinking in the launcher; `hermes tools enable file terminal`).

---

## Stage 6 — Scope-lock: a pure RAG bot (2026-07-09)
**Goal:** Hermes must do NOTHING but search the corpus — no editing/creating/deleting files, no shell,
no web, no code. Every request → either a `search_docs` call or a one-line "I can only search your
research corpus." Enforced two ways (hard + soft):

- **Hard (tool removal)** — the model can't act if the tools don't exist:
  ```powershell
  hermes tools disable file terminal web code_execution computer_use cronjob delegation `
      image_gen memory session_search todo tts vision --platform cli
  hermes tools disable file terminal web code_execution computer_use cronjob delegation `
      image_gen memory session_search todo tts vision --platform discord
  #  + config.yaml mcp_servers.filesystem.enabled: false
  #  Kept enabled: docs (search_docs/docs_status), clarify, messaging (reply), skills (skill loads)
  ```
- **Soft (guidance)** — a `⛔ SCOPE LOCK` block at the top of the skill + the first line of Hermes
  `memories/MEMORY.md`, so it *explains* instead of trying and failing, and never fabricates an action
  result. (Memory is always injected, independent of whether the skill loads.)

**Adversarial test = 5/5 PASS** (see `RAG_TEST_QUESTIONS.md` Set 2). Asked to create summary.txt, delete
chunk.py, run `ls -la`, web-search a price, write+run code → **all refused in one line, no action taken
(verified on disk), no fabricated "done".** One real corpus question still answered correctly = not
over-locked.

## Stage 7 — The reliability rabbit-hole + the key lesson (2026-07-09)
Chasing "sometimes it prints `search_docs(...)` as TEXT instead of calling it" surfaced several traps:

1. **Never background-load torch models under an asyncio MCP server.** The "fast tool registration"
   idea (load BGE-M3/reranker in a `threading.Thread` so the MCP handshake returns in ~2 s) **hangs the
   reranker init** under FastMCP's event loop (process stalls ~734 MB, `_models_ready` never sets, every
   search times out). REVERTED to module-top / main-thread load. Reliability > fast registration.
2. **Greedy `--temp 0` is a trap for tool-calling.** With a large system prompt, temp 0 deterministically
   landed on a degenerate path where Qwen emits the call as a code snippet in `content`. **`--temp 0.3`**
   (off greedy) emits real tool calls. Bisect proof: at temp 0, removing *any* system-prompt section
   flipped text→call; at temp ≥0.2 it called reliably.
3. **Prompt hygiene decides small-model tool-calling.** The real trigger was a **coding-agent scaffold**
   ("# Finishing the job", coding posture) bloating the system prompt to ~24 KB and biasing the model
   toward code-text. Levers: `agent.coding_context: 'off'`, `agent.task_completion_guidance: false`.
4. **THE punchline — it was a test artifact.** The scaffold + a "**Claude Code Persona**" (instead of
   "Hermes Agent Persona") only appeared because I ran `hermes chat` *as a subprocess inside Claude Code*.
   Captured the **standalone Discord gateway's** actual request: clean 14.4 KB "Hermes Agent Persona", no
   coding scaffold. A live Discord query → real `docs_status` + `search_docs` calls → **"four does per
   improved breed"** cited (`no7-1986_PCRD-H001935` p.71). **The real path was fine all along.**
   → **Launch the gateway from a normal terminal / logon startup, never from inside another agent.**
   Reliability fix kept: `_search_lock` (serialize searches; kills the retry "already borrowed" panic),
   `RERANK_N` 40→24, docs MCP `timeout: 120`.

---

## Lessons for the ideal server (read this first when you build it)
Distilled from Stages 1–7. Quality was decided by data + retrieval + **prompt/agent hygiene**, almost
never by raw model size.

**Retrieval stack — keep it, it works.** PyMuPDF triage → **Marker** OCR (SOTA on scans; beat Tesseract)
→ structure-aware chunking (page separators, garble filter, dedupe) → **BGE-M3** dense+sparse →
**LanceDB** (embedded; Tantivy FTS) → hybrid **RRF** → **bge-reranker-v2-m3**. Use **exact/brute-force**
vector search, NOT IVF-PQ (PQ quantization + default nprobes silently dropped small docs and crushed
scores). Reranker is the single biggest quality lever. Rerank on CPU at query time so it coexists with
the serving GPU model.

**Model & serving.**
- Prefer a strong **non-thinking / instruct** model for an agentic RAG bot. The Thinking variant *reasoned*
  correctly but was slow (~113 s) and looped tool calls to the budget. On the ideal server, **Llama-3.3-70B
  (or funded Opus) removes the tool-call fragility entirely** — worth it purely for reliability.
- Serve at **temp ≈ 0.3**, not greedy 0 (greedy → degenerate "tool-call-as-text").
- Give **enough context + tokens** for the model to finish thinking before the tool call (low `max_tokens`
  truncated mid-reason on the 30B).

**Agent / prompt hygiene (this was the real bottleneck, not the model).**
- **Prune competing tools.** For "search my research", models grab `search_files`/web/`session_search`
  over the RAG tool. Give the corpus flow as few tools as possible — ideally only `search_docs`.
- **Keep the system prompt lean.** Coding-agent scaffolding ("finishing the job", big skills catalogs)
  and a wrong product persona destabilise tool-calling. Turn off coding posture for a pure RAG bot.
- **Warm the embedding/reranker models** at server start; never lazy/background-load them under an async
  server loop. First-query latency must stay under the client's MCP call timeout.
- **Serialize the search backend** (one lock) — shared BGE-M3/reranker/lancedb handles aren't concurrency-safe.
- **Scope-lock pattern** (Stage 6): remove tools (hard) + a top-of-prompt rule (soft) + always-injected
  memory. Verify with an **adversarial test** (ask it to act; confirm on disk nothing happened).

**Ops / gotchas.**
- Frontier providers as `model.default` with the local model as *fallback* silently masks which model
  actually runs when the primary is unfunded (401/402/429). Check the real generation model in logs.
- Windows: launch long-lived servers **detached in their own hidden console** (Start-Process) so a parent
  console close can't kill them; auto-resume via a logon Startup `.vbs`, not a Scheduled Task (UAC).
- **Never run the production agent as a subprocess of another agent harness** — env/persona bleed changes
  its system prompt and breaks behaviour (Stage 7 punchline).
- Secrets: keep provider keys out of the committed config; the fallback `sk-proj-…` OpenAI key here is
  plaintext — rotate/externalise on the real server.

**Test discipline.** Hand-built, ground-truth-verified question sets (`RAG_TEST_QUESTIONS.md` Set 1 +
held-out Set 2), graded on 6 axes: tool-use, grounded, cited, no-fabrication, no-conflation, honest
abstention. Faithfulness held across every run (no invented numbers) — the anti-fabrication skill/memory
rules work. Test the **real deployment path** (warm gateway/Discord), not just a cold CLI harness.

---

## 📊 Corpus, hardware & processing benchmarks (at a glance)
Consolidated reference for sizing the future server. All processing below ran on ONE workstation.

### Hardware used for all processing (2026-06 → 07)
| Component | Spec |
|---|---|
| GPU | **NVIDIA RTX 4080 SUPER, 16 GB GDDR6X** (OCR + embedding; Marker peaked ~7.1 GB VRAM — did NOT saturate 16 GB) |
| CPU | **AMD Ryzen 9 5900X**, 12 cores / 24 threads (query-time BGE-M3 encode + reranker run here) |
| RAM | **64 GB** |
| Disk | 1.9 TB SSD (633 GB free); OCR markdown + LanceDB + vectors added tens of GB |
| OS | Windows 11 Pro (10.0.26200), native (no WSL); llama.cpp CUDA build |

### Corpus processed
| Stage | Count |
|---|---|
| Input PDFs | **8,823 files / 22 GB** (Philippine agriculture / aquaculture / rubber / ASF research) |
| Triage split | **1,741 TEXT_READY** (19.7%) · **7,077 NEEDS_OCR+SUSPECT** (80.2%) · 5 corrupt |
| TEXT_READY extracted (PyMuPDF) | 1,741 docs · **38,588 pages** · 60.5 M chars → **52,293 chunks** |
| Scanned OCR'd (Marker) | **7,077 files · 176,745 pages · 486.1 M chars · 0 errors** (59 duplicate files skipped) → **301,311 chunks** |
| **Total indexed (LanceDB)** | **353,604 chunks** (BGE-M3 1024-d dense + Tantivy FTS, exact search) |

### Processing time (measured on the hardware above)
| Job | Time | Rate / notes |
|---|---|---|
| Triage 8,823 PDFs (PyMuPDF, CPU) | ~minutes–1 h | text-layer detection only |
| TEXT_READY extract → chunk → embed → index | ~tens of min | 52,293 chunks, BGE-M3 GPU batch |
| **OCR the 7,077 scanned files (Marker, GPU)** | **≈162 h compute (~6.8 days) · 11.1 days wall-clock w/ pauses** (2026-06-27→07-08) | **3.30 s/page · ~1,090 pages/h**; sequential pipeline (CPU stages) was the bottleneck, not GPU — no free speedup from batch size on the 16 GB card. Biggest file: 1,106 pages / 29 min. Resumable via SQLite manifest across reboots. |
| Embed + index the 301,311 OCR chunks (BGE-M3 GPU) | ~1–2 h | folded into the same LanceDB `research` table |

**Key sizing takeaway for the server:** OCR dominated everything (~6.8 GPU-days on the 4080 for 176k
pages). A single stronger GPU (Ada/Blackwell) OR Marker's true batch path would cut this ~2–4×; a cloud
A100/H100 burst finishes it in well under a day. Everything else (extract, embed, index, query) is
minutes-to-hours and CPU-friendly at query time.

### Tool versions (pinned in `rag\.venv`)
`marker-pdf 1.10.2` · `surya-ocr 0.17.1` · **`torch 2.11.0+cu128`** (CUDA — note PyPI `torch` default is
CPU-only; must use `--index-url https://download.pytorch.org/whl/cu128`) · `FlagEmbedding` (BGE-M3 +
bge-reranker-v2-m3) · `lancedb` (Tantivy FTS) · `langchain-text-splitters` · `pymupdf 1.27.2.3` ·
`Tesseract 5.4.0` (sample/baseline only). Serving: **llama.cpp** (CUDA) → Qwen3-30B-A3B-**Instruct**-2507
Q4_K_M GGUF, `--n-cpu-moe 26`, 64K ctx, q8 KV, `-fa on`, `--jinja`, `--temp 0.3`. Agent: **Hermes Agent**
(MCP client) via the `docs` stdio MCP server (`rag_mcp_server.py`).
