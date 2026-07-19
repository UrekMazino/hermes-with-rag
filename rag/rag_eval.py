"""
Stage 4b — retrieval eval harness (judge-free).

Measures whether **hybrid + rerank** surfaces the right document better than
**dense-only**, using a small set of queries with known-relevant source docs
(verified from RAG_TEST_QUESTIONS.md). Reports hit@k for each method — no LLM
judge needed (this is context-recall, the retrieval half of RAG quality).

Imports the REAL server retrieval logic from rag_mcp_server.py so it tests
exactly what Hermes uses. CPU; needs the LanceDB index + BGE-M3 + reranker.

    cd C:\\Users\\jcvia\\PyCharmMiscProject\\ProjectY\\rag
    .\\.venv\\Scripts\\python.exe .\\rag_eval.py [--k 5]
"""
from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rag_mcp_server as S  # loads BGE-M3 + reranker + opens the table (module-top)

# (query, [substrings that identify a relevant source stem]) — verified ground truth.
EVAL = [
    # [T] text-ready-slice topics
    ("microalgae paste as feed for milkfish hatcheries", ["H004499", "H003426", "H004490"]),
    ("at what age do rubber trees start latex tapping", ["H006842"]),
    ("profitability of tilapia farming pond cage culture", ["H003804"]),
    ("which microalgae species are used to produce the local paste", ["H003426"]),
    ("imported commercial microalgae paste price per liter", ["H004499", "H004480", "H003953"]),
    ("local microalgae paste sold at half the market price", ["H004490", "H004525"]),
    ("coconut scale insect outbreak 2011 million trees CALABARZON", ["H003807", "H003954", "H004574"]),
    # [S] newly-OCR'd scanned-doc topics (ground truth from FTS/keyword)
    ("biological control agent Comperiella against coconut scale insect", ["H003807", "COMPERIELLA"]),
    ("doe-level goat production management manual", ["doe-level Goat", "H003389", "H002128"]),
    ("PhilRubber PRIME rubber investment and market encounter", ["philrubber", "H007706"]),
    ("manually operated HPSD boron treatment equipment for bamboo", ["HPSD", "Multicap"]),
    ("dragon fruit pitaya production in the Philippines", ["Dragon Fruit", "H003548"]),
    ("raising free-range Philippine native chicken", ["Native Chic", "H005162"]),
    ("growth of young robusta coffee with fertilizer application", ["54706"]),
    ("bottom-set tray ocean nursery system for sea cucumber Holothuria scabra",
     ["ocean nursery", "Holothuria", "H006163", "Harnessing"]),
]


def dense_only(q: str, topn: int):
    qv = S._model.encode([q], max_length=512)["dense_vecs"][0]
    rows = S._tbl.search(qv.tolist()).metric("cosine").limit(topn).to_list()
    return [r.get("stem", "") for r in rows]


def hybrid_rerank(q: str, topn: int):
    return [r.get("stem", "") for r, _ in S._search_sync(q, topn)]


def rank_of_first(stems, expected):
    """1-based rank of the first stem matching any expected substring (case-insensitive), else None."""
    exp = [e.lower() for e in expected]
    for i, s in enumerate(stems, 1):
        sl = (s or "").lower()
        if any(e in sl for e in exp):
            return i
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Retrieval eval: dense vs hybrid+rerank")
    ap.add_argument("--k", type=int, default=5, help="hit@k threshold")
    ap.add_argument("--topn", type=int, default=10, help="depth to compute ranks over")
    args = ap.parse_args()
    k, topn = args.k, args.topn

    print(f"Reranker loaded: {S._reranker is not None} | rows: {S._tbl.count_rows():,}\n")
    print(f"{'query':<46}{'dense rank':>12}{'hybrid+rr rank':>16}")
    print("-" * 74)
    agg = {"dense": [0, 0, 0.0], "hybrid": [0, 0, 0.0]}  # hit@1, hit@k, MRR-sum
    t0 = time.time()
    for q, exp in EVAL:
        dr = rank_of_first(dense_only(q, topn), exp)
        hr = rank_of_first(hybrid_rerank(q, topn), exp)
        for key, rk in (("dense", dr), ("hybrid", hr)):
            if rk == 1: agg[key][0] += 1
            if rk and rk <= k: agg[key][1] += 1
            agg[key][2] += (1.0 / rk) if rk else 0.0
        print(f"{q[:44]:<46}{(str(dr) if dr else '>'+str(topn)):>12}{(str(hr) if hr else '>'+str(topn)):>16}")
    n = len(EVAL)
    d, h = agg["dense"], agg["hybrid"]
    print("-" * 74)
    print(f"{'metric':<46}{'dense':>12}{'hybrid+rr':>16}")
    print(f"{'  hit@1 (rank-1 precision)':<46}{f'{d[0]}/{n}':>12}{f'{h[0]}/{n}':>16}")
    print(f"{'  hit@' + str(k):<46}{f'{d[1]}/{n}':>12}{f'{h[1]}/{n}':>16}")
    print(f"{'  MRR':<46}{d[2]/n:>12.3f}{h[2]/n:>16.3f}")
    print(f"\nelapsed {time.time()-t0:.1f}s ({(time.time()-t0)/n:.2f}s/query incl. rerank)")
    print("rank = position of the first known-relevant source doc (lower is better)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
