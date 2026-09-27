"""Compare rerankers on this corpus, with ground truth and no LLM.

WHY THIS CAN BE TRUSTED WHEN THE END-TO-END EVAL CANNOT. Generation is
stochastic -- the project measured the same suite at 25/34 and 22/34 on two
runs of identical code. Reranking is deterministic, so a difference here is
a real difference. It also needs no provider, which matters while the
gateway is unreachable.

GROUND TRUTH comes from eval_cases_retrieval.json, which already carries
`keywords_all` per question ("eight", "FAT32", "IF5", "PA04138"). A chunk is
RELEVANT if it contains all of them. That is the same standard the suite
uses to decide whether an answer was right, so it is the project's own
definition rather than one invented here.

REPORTED per model:
  recall@1   the correct chunk ranked first -- what decides most answers,
             since the answering prompt now tells the model passage 1 is
             the most likely to hold the answer
  recall@3 / recall@8   8 is CONTEXT_K, so recall@8 is "did it reach the
             model at all"
  MRR        1/rank of the first relevant chunk, averaged
  sec/query  reranking only. This is CPU-only, so a large model can be
             correct and still unusable.

USAGE
  cd src && ../.venv/Scripts/python.exe ../tools/bench_reranker.py
  ../.venv/Scripts/python.exe ../tools/bench_reranker.py --models a,b,c
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../src")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from dotenv import load_dotenv
load_dotenv(".env")

import reranker
from retrieval_db import retrieve_from_db

CANDIDATES = [
    "cross-encoder/ms-marco-MiniLM-L-6-v2",     # current, 22M
    "cross-encoder/ms-marco-MiniLM-L-12-v2",    # same family, 33M
    "BAAI/bge-reranker-base",                   # 278M, 512 ctx
]

FETCH = 16          # RETRIEVE_K
CONTEXT_K = 8


def relevant(text, keywords):
    """A keywords_all entry is either a literal substring or a list of
    alternatives (any-of, for a fact the model may phrase more than one
    way) -- same shape eval.py and eval_retrieval.py match against."""
    low = (text or "").lower()
    for k in keywords:
        alts = k if isinstance(k, list) else [k]
        if not any(str(alt).lower() in low for alt in alts):
            return False
    return True


def main():
    models = CANDIDATES
    for i, a in enumerate(sys.argv):
        if a == "--models" and i + 1 < len(sys.argv):
            models = [m.strip() for m in sys.argv[i + 1].split(",") if m.strip()]

    cases = json.load(open("eval_cases_retrieval.json", encoding="utf-8"))["cases"]
    cases = [c for c in cases if c.get("keywords_all")]
    print(f"{len(cases)} cases with keyword ground truth\n")

    # Retrieve ONCE per case: the candidate set is identical for every
    # reranker, so any difference is the reranker's and nothing else's.
    fetched = []
    for c in cases:
        raw = retrieve_from_db(c["q"], top_k=FETCH,
                               scope={"product": c["product"]} if c.get("product") else None)
        has_answer = any(relevant(r.get("text"), c["keywords_all"]) for r in raw)
        fetched.append((c, raw, has_answer))

    reachable = sum(1 for _, _, h in fetched if h)
    print(f"ceiling: {reachable}/{len(cases)} cases have a relevant chunk in the "
          f"top {FETCH} at all — no reranker can beat this\n")

    print("%-42s %7s %7s %7s %7s %9s" %
          ("model", "r@1", "r@3", "r@8", "MRR", "sec/q"))
    print("-" * 84)

    results = {}
    for name in models:
        try:
            # The ENVIRONMENT variable, not the module global: rerank()
            # calls _get() with no argument, which resolves through
            # _configured(), and _configured() checks os.getenv
            # ("RERANKER_MODEL") FIRST -- reranker.RERANKER_MODEL (the
            # module attribute, only ever read at import time) is not what
            # it looks at. Setting only that attribute made every model
            # measure MiniLM three times: whatever _get(name) loaded here
            # was undone the moment rerank() called _get() with no
            # argument and fell through to policy's reranker_profile
            # ("fast" -> MiniLM), a few lines later.
            os.environ["RERANKER_MODEL"] = name
            reranker._model = None
            reranker._loaded = None
            reranker._get(name)
        except Exception as e:
            print("%-42s  COULD NOT LOAD: %s" % (name, str(e)[:30]))
            continue
        r1 = r3 = r8 = 0
        rr_total = 0.0
        t0 = time.time()
        misses = []
        for c, raw, has_answer in fetched:
            ranked = reranker.rerank(c["q"], raw, top_k=len(raw))
            pos = None
            for i, r in enumerate(ranked, 1):
                if relevant(r.get("text"), c["keywords_all"]):
                    pos = i
                    break
            if pos:
                rr_total += 1.0 / pos
                r1 += pos <= 1
                r3 += pos <= 3
                r8 += pos <= CONTEXT_K
                if pos > CONTEXT_K:
                    misses.append((c["q"][:44], pos))
            elif has_answer:
                misses.append((c["q"][:44], "lost"))
        secs = (time.time() - t0) / max(1, len(fetched))
        n = len(fetched)
        print("%-42s %6d%% %6d%% %6d%% %7.3f %8.2fs" %
              (name[:42], 100*r1//n, 100*r3//n, 100*r8//n, rr_total/n, secs))
        results[name] = (r1, r3, r8, rr_total / n, secs, misses)

    print("\nCases where the relevant chunk did NOT reach the context (top %d):"
          % CONTEXT_K)
    for name, (_, _, _, _, _, misses) in results.items():
        print("  %s" % name)
        if not misses:
            print("      none")
        for q, pos in misses:
            print("      rank %-5s %s" % (pos, q))


if __name__ == "__main__":
    main()
