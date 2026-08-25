#!/usr/bin/env python3
"""eval_retrieval.py — measure RETRIEVAL, separately from generation.

WHY THIS EXISTS

eval.py scores the whole pipeline end to end, which means a retrieval
regression, a generation regression and an over-strict grounding threshold all
look the same: a failed case. It also needs a live backend and a working
provider key, so when the key is wrong there is no signal at all.

Retrieval quality does not need any of that. The embedder and the reranker run
locally, so recall@k and MRR are measurable offline, for free, on every change
to chunking, parsing, or the fusion step. That is the cheapest feedback loop in
the system and it was not being collected.

WHAT IT MEASURES

For each case in eval_cases.json that is scoped to a product, the document(s)
filed under that product in the catalogue are the expected sources. A case
passes at k if any expected source appears in the top k retrieved chunks.

  recall@k  fraction of cases whose expected document appears in the top k
  MRR       mean reciprocal rank of the first correct document

Both are reported before AND after reranking, because that isolates whether
the reranker is earning its latency.

USAGE

  python eval_retrieval.py                    # measure at current settings
  python eval_retrieval.py --json out.json    # also write machine-readable
  python eval_retrieval.py --compare out.json # diff against an earlier run
  CONTEXT_K=5 python eval_retrieval.py        # sweep a setting

Run with the backend stopped: it opens the vector store directly.
"""
import json
import logging
import os
import sys

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
for noisy in ("chromadb", "sentence_transformers", "httpx", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

HERE = os.path.dirname(os.path.abspath(__file__))
KS = (1, 3, 5, 8)


def catalog_sources_by_product() -> dict[str, set[str]]:
    with open(os.path.join(HERE, "catalog_config.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    out: dict[str, set[str]] = {}
    for cat in (cfg.get("categories") or []):
        for prod in (cat.get("products") or []):
            key = prod.get("key")
            if not key:
                continue
            out.setdefault(key, set()).update(
                os.path.basename(s) for s in (prod.get("sources") or []))
    return out


def load_cases() -> list[dict]:
    with open(os.path.join(HERE, "eval_cases.json"), encoding="utf-8") as fh:
        return json.load(fh).get("cases") or []


def first_hit_rank(results: list[dict], expected: set[str],
                   keywords: list[str] | None) -> int | None:
    """1-indexed rank of the first chunk that actually carries the answer.

    Document-level matching is degenerate under production scoping: retrieval
    filters to the one document filed under the product, so every returned
    chunk is "correct" and recall is trivially 1.000. What decides answer
    quality is whether the chunk holding the answer reached the prompt, so a
    hit is a chunk whose TEXT contains an expected keyword. Falls back to
    document matching only for cases with no keywords to check.
    """
    for i, r in enumerate(results, start=1):
        if keywords:
            text = (r.get("text") or "").lower()
            if any(k.lower() in text for k in keywords):
                return i
        elif os.path.basename(r.get("source") or "") in expected:
            return i
    return None


def measure() -> dict:
    from retrieval_db import retrieve_from_db
    from reranker import rerank
    import main as app          # for RETRIEVE_K / CONTEXT_K, one source of truth

    by_product = catalog_sources_by_product()
    cases = load_cases()

    scored, skipped = [], []
    for case in cases:
        product = case.get("product")
        if not product:
            skipped.append({"q": case["q"], "why": "no product scope"})
            continue
        expected = by_product.get(product) or set()
        if not expected:
            skipped.append({"q": case["q"],
                            "why": f"no document filed under {product!r}"})
            continue
        # A case that SHOULD be refused has no correct document to retrieve.
        if case.get("outcome") == "rejected":
            skipped.append({"q": case["q"], "why": "refusal case"})
            continue

        # scope must be a DICT -- _matches_scope does `"product" in scope`, so a
        # bare string is a substring test that silently matches nothing and
        # disables filtering altogether. Passing the string is how an earlier
        # run measured the whole corpus while believing it was scoped.
        keywords = case.get("keywords_all") or []
        raw = retrieve_from_db(case["q"], top_k=app.RETRIEVE_K,
                               scope={"product": product})
        ranked = rerank(case["q"], raw, top_k=app.CONTEXT_K)

        scored.append({
            "q": case["q"],
            "product": product,
            "expected": sorted(expected),
            "keywords": keywords,
            "rank_retrieved": first_hit_rank(raw, expected, keywords),
            "rank_reranked": first_hit_rank(ranked, expected, keywords),
            "n_retrieved": len(raw),
        })

    def agg(field: str) -> dict:
        ranks = [c[field] for c in scored]
        found = [r for r in ranks if r]
        out = {f"recall@{k}": round(sum(1 for r in found if r <= k) / len(ranks), 3)
               for k in KS} if ranks else {}
        out["mrr"] = round(sum(1 / r for r in found) / len(ranks), 3) if ranks else 0.0
        out["misses"] = sum(1 for r in ranks if not r)
        return out

    return {
        "settings": {
            "retrieve_k": app.RETRIEVE_K,
            "context_k": app.CONTEXT_K,
            "chunk_char_cap": app.CHUNK_CHAR_CAP,
        },
        "cases_scored": len(scored),
        "cases_skipped": len(skipped),
        "retrieved": agg("rank_retrieved"),
        "reranked": agg("rank_reranked"),
        "detail": scored,
        "skipped": skipped,
    }


def show(res: dict) -> None:
    s = res["settings"]
    print(f"settings   retrieve_k={s['retrieve_k']} context_k={s['context_k']} "
          f"chunk_char_cap={s['chunk_char_cap']}")
    print(f"cases      {res['cases_scored']} scored, {res['cases_skipped']} skipped")
    print()
    hdr = f"{'STAGE':<12}" + "".join(f"{'R@'+str(k):>8}" for k in KS) + f"{'MRR':>8}{'MISS':>7}"
    print(hdr)
    print("-" * len(hdr))
    for stage in ("retrieved", "reranked"):
        a = res[stage]
        if not a:
            continue
        row = f"{stage:<12}" + "".join(f"{a.get('recall@'+str(k), 0):>8.3f}" for k in KS)
        print(row + f"{a['mrr']:>8.3f}{a['misses']:>7}")
    print()

    misses = [c for c in res["detail"] if not c["rank_reranked"]]
    if misses:
        print(f"Not retrieved at all ({len(misses)}):")
        for c in misses:
            print(f"  [{c['product']}] {c['q']}")
        print()

    demoted = [c for c in res["detail"]
               if c["rank_retrieved"] and c["rank_reranked"]
               and c["rank_reranked"] > c["rank_retrieved"]]
    if demoted:
        print(f"Reranker pushed the right document DOWN ({len(demoted)}):")
        for c in demoted:
            print(f"  {c['rank_retrieved']} -> {c['rank_reranked']}  {c['q']}")
        print()


def compare(new: dict, old_path: str) -> int:
    with open(old_path, encoding="utf-8") as fh:
        old = json.load(fh)
    print(f"comparing against {old_path}")
    print(f"{'METRIC':<16}{'BEFORE':>9}{'AFTER':>9}{'DELTA':>9}")
    print("-" * 43)
    regressed = 0
    for stage in ("retrieved", "reranked"):
        for k in [f"recall@{k}" for k in KS] + ["mrr"]:
            b = (old.get(stage) or {}).get(k)
            a = (new.get(stage) or {}).get(k)
            if b is None or a is None:
                continue
            d = round(a - b, 3)
            flag = ""
            if d < -0.001:
                flag, regressed = "  REGRESSED", regressed + 1
            print(f"{stage[:3]+' '+k:<16}{b:>9.3f}{a:>9.3f}{d:>+9.3f}{flag}")
    print()
    if regressed:
        print(f"{regressed} metric(s) regressed.")
        return 1
    print("No regression.")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    res = measure()

    if res["cases_scored"] == 0:
        print("No cases could be scored. Either the index is empty (rebuild "
              "with `python reindex.py --from-store`) or no eval case carries "
              "a `product` that has a document filed under it.")
        if res["skipped"]:
            print("\nSkipped:")
            for s in res["skipped"][:10]:
                print(f"  {s['why']}: {s['q'][:60]}")
        return 1

    show(res)

    if "--json" in argv:
        i = argv.index("--json")
        path = argv[i + 1] if i + 1 < len(argv) else "retrieval_metrics.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2)
        print(f"written to {path}")

    if "--compare" in argv:
        i = argv.index("--compare")
        if i + 1 >= len(argv):
            print("--compare needs a path to an earlier --json run")
            return 2
        return compare(res, argv[i + 1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
