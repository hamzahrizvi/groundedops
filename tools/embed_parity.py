"""Does the onnx text encoder give the same vectors as the torch one? (D-20261023-06)

Measures REPO's default `EMBED_BACKEND=onnx` (embeddings._OnnxEmbedder) against SentenceTransformer on
identical text: a sample of indexed chunks (batches of 16, as ingest does) and the questions of a run
(via retrieval_db._dense_ranking, the production search path, unscoped and product-scoped).
Questions are embedded as typed; follow-ups are NOT condensed here.

  CHROMA_DIR=<copy of src/chroma_db> GO_CODE=<REPO>/src GO_DATA=<REPO>/src
    python tools/lab_lock.py python tools/embed_parity.py --cases <run>/cases.json --chunks 200 --out parity.json

GO_CODE must be a src with the onnx backend (commit 7ecdaaf). Never run against the live chroma_db.
VERDICT FINDING when: min chunk cosine onnx-vs-torch < 0.99, any question's query cosine < 0.99, or any
question's top-10 overlap < min(9, n) (n = ids the onnx side returned, so a scope with 3 hits needs all 3).
"""
import argparse
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.abspath(os.environ.get("GO_CODE", os.path.join(HERE, "..", "src")))
DATA = os.path.abspath(os.environ.get("GO_DATA", CODE))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, CODE)
os.chdir(DATA)


def overlap(a, b):
    return len(set(a) & set(b))


def verdict(min_chunk_cos, rows):
    if min_chunk_cos < 0.99:
        return "FINDING"
    for r in rows:
        if r["query_cos"] < 0.99:
            return "FINDING"
        for k, n in (("top10", "n"), ("top10_scoped", "n_scoped")):
            if r[k] is not None and r[k] < min(9, r[n]):
                return "FINDING"
    return "CLEARED"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--chunks", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--limit-cases", type=int, default=0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import numpy as np
    import embeddings, db, retrieval_db, model_pins  # noqa: E401
    if not hasattr(embeddings, "_OnnxEmbedder"):
        print("GO_CODE has no onnx backend (needs REPO src with commit 7ecdaaf)")
        sys.exit(2)
    # torch first, on the main thread; onnx built directly so a broken load raises instead of comparing torch with torch
    from sentence_transformers import SentenceTransformer
    torch_m = SentenceTransformer(embeddings.EMBED_MODEL, revision=model_pins.revision(embeddings.EMBED_MODEL))
    onnx_m = embeddings._OnnxEmbedder(embeddings.EMBED_MODEL)
    backends = [("onnx", onnx_m), ("torch", torch_m)]

    col = db.get_collection()
    all_ids = sorted(col.get(include=[])["ids"])
    sample = random.Random(a.seed).sample(all_ids, min(a.chunks, len(all_ids)))
    got = col.get(ids=sample, include=["documents", "embeddings"])
    ids, docs = got["ids"], got["documents"]
    stored = np.asarray(got["embeddings"], dtype=np.float32)
    stored = stored / np.linalg.norm(stored, axis=1, keepdims=True)

    vecs, cps = {}, {}
    for name, m in backends:
        t0 = time.perf_counter()
        parts = []
        for i in range(0, len(docs), 16):
            parts.append(m.encode(docs[i:i + 16], convert_to_numpy=True, normalize_embeddings=True))
            print(f"{name} {min(i + 16, len(docs))}/{len(docs)}", flush=True)
        vecs[name] = np.concatenate(parts)
        cps[name] = len(docs) / (time.perf_counter() - t0)
    cos = {"onnx_vs_torch": (vecs["onnx"] * vecs["torch"]).sum(1),
           "onnx_vs_stored": (vecs["onnx"] * stored).sum(1),
           "torch_vs_stored": (vecs["torch"] * stored).sum(1)}
    summary = {k: {"min": float(v.min()), "p1": float(np.percentile(v, 1)), "median": float(np.median(v))}
               for k, v in cos.items()}
    worst = [{"id": ids[i], "cos": float(cos["onnx_vs_torch"][i]), "len": len(docs[i])}
             for i in np.argsort(cos["onnx_vs_torch"])[:5]]

    cases = json.load(open(a.cases, encoding="utf-8"))["cases"]
    if a.limit_cases > 0:
        cases = cases[:a.limit_cases]
    res = {}
    for name, m in backends:
        embeddings._model = m
        embeddings._embed_query_cached.cache_clear()
        res[name] = []
        for c in cases:
            q, p = c["q"], c.get("product")
            res[name].append((embeddings.embed_query(q),
                              retrieval_db._dense_ranking(q, col, 10, None, None),
                              retrieval_db._dense_ranking(q, col, 10, None, {"product": p}) if p else None))
    rows = []
    for c, (vo, uo, so), (vt, ut, st) in zip(cases, res["onnx"], res["torch"]):
        r = {"id": c["id"], "product": c.get("product"), "type": c.get("type"), "query_cos": float(vo @ vt),
             "top10": overlap(uo, ut), "n": min(10, len(uo)), "top1_same": uo[:1] == ut[:1],
             "top10_scoped": None, "n_scoped": None, "top1_scoped_same": None}
        if so is not None and (so or st):
            r.update(top10_scoped=overlap(so, st), n_scoped=min(10, len(so)), top1_scoped_same=so[:1] == st[:1])
        rows.append(r)

    v = verdict(summary["onnx_vs_torch"]["min"], rows)
    print(f"index chunks {len(all_ids)}, sampled {len(ids)}; chunks/s onnx {cps['onnx']:.2f} torch {cps['torch']:.2f}")
    print("(questions embedded as typed, follow-ups not condensed)")
    for k, s in summary.items():
        print(f"{k:16s} min {s['min']:.5f}  p1 {s['p1']:.5f}  median {s['median']:.5f}")
    print("worst 5 chunks (onnx vs torch):")
    for w in worst:
        print(f"  {w['id']} cos {w['cos']:.5f} len {w['len']}")
    for r in rows:
        sc = "-" if r["top10_scoped"] is None else f"{r['top10_scoped']}/{r['n_scoped']}"
        print(f"{r['id']} {r['product']} {r['type']} cos {r['query_cos']:.5f} top10 {r['top10']}/{r['n']} "
              f"scoped {sc} top1 {r['top1_same']}")
    if rows:
        print(f"lowest query cos {min(r['query_cos'] for r in rows):.5f}; lowest top10 {min(r['top10'] for r in rows)}")
    print(f"VERDICT: {v}")
    json.dump({"summary": summary, "worst_chunks": worst, "rows": rows, "verdict": v, "chunks_per_s": cps,
               "n_index": len(all_ids), "n_sampled": len(ids), "seed": a.seed, "cases_path": a.cases},
              open(a.out, "w", encoding="utf-8"), indent=1)
    print("DONE")


if __name__ == "__main__":
    main()
