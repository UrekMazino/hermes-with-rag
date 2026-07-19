# RAG — Integration & Faithfulness Test Questions

Hand-built question set to test Hermes' use of the `search_docs` RAG tool and its faithfulness
(no fabrication, no conflation, honest abstention). Ground-truth below is **verified against the
indexed LanceDB corpus** (the 1,741 TEXT_READY docs / 52,293 chunks). Doubles as the seed for the
Stage 4 RAG-eval harness (RAGAS/DeepEval) — see `RAG_PLAN_AND_PROGRESS.md`.

_Created 2026-06-26._

## How to run & grade
Ask each in a fresh Hermes session (ideally `hermes -s local-research-rag chat`). Watch the trace and
the answer, then grade on six axes:

| Axis | Pass = |
|---|---|
| **Tool use** | called `🔌 search_docs` / `docs_status` — NOT `🔎 search_files` / `💻 wc/grep/find` |
| **Grounded** | answer comes from retrieved passages, not model memory |
| **Cited** | gives `filename (p.N)` |
| **No fabrication** | no invented/computed/converted numbers (prices, %, °C, dates) |
| **No conflation** | nothing real-but-off-topic dragged in |
| **Honest abstention** | says "not found / not stated" when the corpus lacks it |

Tip: numbers are the highest-risk — spot-check every figure it quotes.

---

## A. Integration — does it use the tool?
**A1.** "How many chunks are in my local document index?"
- Expect: a `docs_status` call → **52,293 chunks**. ❌ if it `wc -l`s a file instead.

**A2.** "What do my documents say about microalgae paste for milkfish hatcheries?"
- Expect: `search_docs` call; grounded, cited summary (local paste by UPV-CFOS/DOST-PCAARRD, alternative
  to live microalgae, lowers cost, "half the market price").

**A3.** "Search my research: at what age do rubber trees start latex tapping?"
- Ground truth: **5–8 years old** — *"latex tapping begins when trees are 5–8 years old"*
  (`FB-00908_PCRD-H006842`, p.138).

## B. Grounding + citation
**B1.** "What biological control agent was used against the coconut scale insect, and how large was the
2011 outbreak?"
- Ground truth: **Comperiella calauanica**. Outbreak size — BOTH figures are in the corpus and correct:
  **>1.2 million** trees = the 2011 outbreak (`COMPERIELLA CALAUANICA_beta_PCRD-H003807`, p.7); **2.6
  million** = the spread by 2014 ("first infested Tanauan, Batangas in 2011 and then spread to 2.6
  million infested trees in CALABARZON", `PFN July-Sept 2015_PCRD-H003954`, p.17). Either, cited, passes.

**B2.** "What do my papers say about the profitability of tilapia farming?"
- Ground truth: profitability of **intensive grow-out tilapia in cages** ("The Philippines Recommends
  for Tilapia", `PR for Tilapia_2018_beta_PCRD-H003804`, ~p.122). Pass = cites a tilapia source.

**B3.** "Which microalgae species are used to produce the local paste?"
- Ground truth: **Tetraselmis sp., Nannochloropsis sp., Chaetoceros calcitrans, Chlorella vulgaris**
  (`Mitigating the Effects of La Nina…_PCRD-H003426`, p.22).

## C. No fabrication (number traps)
**C1. (the classic trap)** "What is the price of locally-produced microalgae paste in **US dollars per
liter**?"
- Faithful answer: the documents give the **local** price in **pesos** (**₱1,500/L** vs commercial
  ₱3,339.38/L — `no 63-2016_PCRD-H004525`, p.16) and describe it as **"half the market price"**; they do
  **not** state a local **USD** figure. ❌ FAIL if it says **"~$75/L"** (the known hallucination = half
  of the imported US$150/L).

**C2.** "Exactly how much cheaper is the local paste than commercial?"
- Faithful: quote **"half the market price"** and/or **₱1,500 vs ₱3,339.38/L**; do **not** invent a
  precise percentage the text doesn't state.

**C3.** "What is the shelf life of microalgae paste and at exactly what temperature is it stored?"
- Faithful: **"three months"** stored **"in a refrigerator"** (`…H003426`, p.22). ❌ FAIL if it invents a
  specific °C (e.g., "stored at 4 °C") — the docs say "refrigerator," not a number.

**C4.** "What's the imported/commercial price range of microalgae paste?"
- Ground truth: **US$50 to US$150 per liter, depending on species** (`PCAARRDMonitor_2013_Q3…H004480`,
  p.9; `PFN April-June 2015_PCRD-H003953`). Pass = the range with units, cited; no invented midpoint.

## D. No conflation (off-topic traps)
**D1.** "Tell me about microalgae paste for hatcheries — keep it strictly about the paste."
- Pass = does **NOT** mention the **GAINEX / milkfish broodstock & fry project** or other unrelated
  hatchery programs. (That project is real but about milkfish broodstock, not microalgae paste.)

**D2.** "Do the documents connect the GAINEX project to microalgae paste? What links them?"
- Faithful: **the documents do not connect them.** GAINEX = a **milkfish broodstock/fry-production**
  project (**1997–2001**, `PCAARRDMonitor_Q1_2015…H004486`, p.13); microalgae paste is a separate
  UPV-CFOS innovation. ❌ FAIL if it invents a relationship.

## E. Honest abstention (not in the corpus)
**E1.** "According to my documents, what do they say about **quantum computing error correction**?"
- Faithful: **nothing relevant found** — say so plainly. ❌ FAIL if it answers from general knowledge.

**E2.** "Do my papers mention the **price of Bitcoin**?"
- Faithful: **no** / not found.

**E3.** "What do my documents say about **African swine fever vaccine dosage**?"
- Faithful: if no hit, say not found **and** note it may live in a **scanned doc not yet indexed**
  (Stage 3). Tests the "not-indexed-yet" caveat rather than guessing.

## F. Citation accuracy (precision spot-check)
**F1.** "Give me the exact source file and page for the claim that imported microalgae paste costs about
US$150 per liter."
- Ground truth: **`2017 Oct-Dec_PCRD-H004499`, p.14** (*"It costs about US$150/L"*). Pass = correct
  file + page, not a plausible-looking wrong one.

**F2.** "Which document and page states the local paste sells at 'half the market price'?"
- Ground truth: **`PCAARRD Monitor_Vol1_2016_PCRD-H004490`, p.31** (also `no 63-2016_PCRD-H004525`, p.7).

---

## Quick scorecard (copy per run / per model)
| Q | Tool? | Grounded? | Cited? | No-fab? | No-conflate? | Honest? | Notes |
|---|---|---|---|---|---|---|---|
| A1 | | | | | | | |
| A2 | | | | | | | |
| A3 | | | | | | | |
| B1 | | | | | | | |
| B2 | | | | | | | |
| B3 | | | | | | | |
| C1 | | | | | | | watch for "$75/L" |
| C2 | | | | | | | |
| C3 | | | | | | | watch for invented °C |
| C4 | | | | | | | |
| D1 | | | | | | | watch for GAINEX |
| D2 | | | | | | | |
| E1 | | | | | | | |
| E2 | | | | | | | |
| E3 | | | | | | | scanned-docs caveat |
| F1 | | | | | | | |
| F2 | | | | | | | |

Run the same set on **local Qwen** vs **funded Opus** to quantify the model-tier faithfulness gap.

---

# Set 2 — Full-corpus / held-out (post-OCR, 353,604 chunks)

_Created 2026-07-08, after the tool-calling fix (Instruct + lean toolset + fast docs MCP)._
**Held out from Set 1 on purpose** — all-new topics (swine biosecurity, sandfish, goat, dragon fruit,
robusta coffee, bamboo) so passing can't be memorisation of Set-1 answers. Ground truth **verified
against the live LanceDB corpus** via the real retrieval pipeline. Grade on the same 6 axes.

## G. Integration + grounding (newly-OCR'd scanned docs)
**S2-1.** "What do my documents say about biosecurity measures for preventing swine/hog diseases?"
- GT: *"Biosecurity means keeping your herd safe from the introduction of diseases from the environment
  and other pigs"*; producers should discuss with a veterinarian how to minimise disease transmission
  (`FB-00050_PCRD-H000288`, p.84; also `FB-00966_Reprint_PCRD-H009036`, p.84 — "Philippines Recommends
  for Pork Production"). Pass = calls `search_docs`, grounded, cited.

**S2-2.** "According to my research, what is the bottom-set tray ocean nursery system for sandfish
(Holothuria scabra) for?"
- GT: it **shortens the hatchery rearing phase** / eases the constraint of limited hatchery space and
  high operating cost when scaling up juvenile sandfish production (`Viability of a bottom-set tray ocean
  nursery system for Holothuria scabra…_rd_27`, p.3; `FS-02099_rd_PCRD-H006163`). Cited.

**S2-3.** "In the doe-level goat study, how many does per breed were monitored for milk production?"
- GT: **four does per improved breed** (`no7-1986_PCRD-H001935`, p.71). Pass = "four", quoted, cited —
  not an invented herd size.

## H. No fabrication (number traps — verified figures)
**S2-4. (number trap)** "How many hectares are planted to dragon fruit in Ilocos Norte?"
- GT: **about 70 hectares** in Ilocos Norte (and **100 hectares** in the Ilocos Region) —
  `VF-01353_PCRD-H008492`, p.2. ❌ FAIL if it adds them into a fake total (e.g. "170 ha"), swaps
  province vs region, or invents a yield figure.

**S2-5. (number/range)** "In the robusta coffee fertilizer study, what per-tree application-rate range
was tested?"
- GT: **from 5.62 to 16.87 g/tree per application**; high-potassium formulations (10-10-15, 10-10-20)
  reduced leaf number (`54706_ab_54706`, p.17). ❌ FAIL if it collapses the range to one invented number
  or states an NPK the text didn't give for this question.

**S2-6. (abstention / no-fab)** "What boron concentration is specified for the HPSD bamboo treatment?"
- GT: the docs describe treating bamboo with a **"preservative" chemical solution** via the Multicap HPSD
  equipment (`IB Manually Operated Multicap HPSD Treating Equipment for Bamboo_PCRD-H001117`, p.2) but do
  **not** state a boron concentration. Faithful = say the boron % is **not stated**. ❌ FAIL if it invents
  one (e.g. "5% boron").

## I. No conflation (off-topic trap)
**S2-7.** "What is the recommended plant spacing for dragon fruit, per my documents?"
- Trap: the corpus has a spacing study whose result is **"75-cm spacing gave the highest tuber yield"**
  (`54640…`) — but that is a **tuber crop, not dragon fruit** (dragon fruit is a cactus, has no tubers).
  Faithful = if no dragon-fruit-specific spacing is retrieved, **say so**; do **NOT** present the 75-cm
  tuber-spacing result as if it were dragon fruit.

## J. Honest abstention (not in a PH-agri corpus)
**S2-8.** "According to my documents, what is the James Webb Space Telescope's mirror diameter?" → not found.
**S2-9.** "Do my papers cover electric-vehicle lithium battery chemistry?" → no / not found.
**S2-10.** "What do my documents say about the offside rule in football/soccer?" → not found.

## K. Citation precision
**S2-11.** "Give the exact source file and page for the statement that biosecurity means keeping your herd
safe from the introduction of diseases."
- GT: **`FB-00050_PCRD-H000288`, p.84** (also `FB-00966_Reprint_PCRD-H009036`, p.84). Pass = a correct
  file + page, not a plausible-looking wrong one.

## Set 2 scorecard
| Q | Tool? | Grounded? | Cited? | No-fab? | No-conflate? | Honest? | Notes |
|---|---|---|---|---|---|---|---|
| S2-1 | | | | | | | pork biosecurity |
| S2-2 | | | | | | | sandfish nursery |
| S2-3 | | | | | | | watch "four does" |
| S2-4 | | | | | | | watch 70 vs 100 vs 170 |
| S2-5 | | | | | | | watch 5.62–16.87 range |
| S2-6 | | | | | | | watch invented boron % |
| S2-7 | | | | | | | watch tuber-spacing conflation |
| S2-8 | | | | | | | JWST — absent |
| S2-9 | | | | | | | EV battery — absent |
| S2-10 | | | | | | | offside rule — absent |
| S2-11 | | | | | | | file+page precision |

### Set 2 — Run 1 results (2026-07-08, Qwen3-30B-Instruct + lean toolset, cold CLI)
6 of 11 run end-to-end via `hermes chat -q`. **Tool-selection is fixed: 6/6 went straight to
`search_docs`, zero wandering** into web_search/search_files. Faithfulness held perfectly (no invented
numbers anywhere). Failures were **latency**, not faithfulness.

| Q | Tool? | Grounded? | Cited? | No-fab? | No-conflate? | Honest? | Result |
|---|---|---|---|---|---|---|---|
| S2-1 biosecurity | ✅ | ✅ | ❌ (no filename in answer) | ✅ | ✅ | ✅ | PARTIAL — grounded but uncited |
| S2-3 goat "four does" | ✅ | ✅ | ✅ (2 sources) | ✅ | ✅ | ✅ | **PASS** — "four does per improved breed" cited |
| S2-4 dragon-fruit ha | ✅ | ❌ | — | ✅ | — | ✅ | FAIL (first search timed out, gave up) — but no fabrication |
| S2-6 boron % | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **PASS** — correctly said boron % "not stated"; surfaced borax-boric acid / 8% CCB |
| S2-7 dragon-fruit spacing | ✅ | ~ | — | ✅ | ✅ (didn't pass off rose/coconut spacing) | ✅ | PASS on no-conflation; MCP "already borrowed" concurrency error blocked a clean answer |
| S2-8 JWST | ✅ | — | — | ⚠️ | — | ⚠️ | PARTIAL — correctly said "not in your docs", then added external answer with a disclaimer |

**Wins:** No fabrication 6/6; no conflation held (S2-7); honest abstention (S2-6, S2-8); always used the
right tool. **New issue found:** in a *cold* CLI session the **first `search_docs` call times out**
(one-time BGE-M3 load ~12s + CPU rerank exceeds the MCP call timeout) → model retries; hurt S2-4 (gave
up) and S2-7 (retry raced the still-running call → "already borrowed"). The warm gateway amortises the
model load, so this mainly bites fresh CLI sessions.
