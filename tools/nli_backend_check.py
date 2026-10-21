"""Does the ONNX NLI checker (NLI_BACKEND=onnx) agree with PyTorch, and is it faster?

Replays the answer units from logged runs against the premises each answer was
grounded on, scores them with both backends, and compares verdicts and scores.

  python tools/nli_backend_check.py OPS/runs/20261020 OPS/runs/20261021b
"""
import argparse, glob, json, os, statistics, sys, time

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
os.chdir(SRC)
sys.path.insert(0, SRC)


def build_units(runs, max_pairs):
    import grounding, text_utils
    units, seen, pairs = [], set(), 0
    for run in runs:
        manuals = [open(f, encoding="utf-8", errors="replace").read()
                   for f in glob.glob(os.path.join(run, "manuals", "*.txt"))]
        with open(os.path.join(run, "app_logs.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                if pairs >= max_pairs:
                    return units, pairs
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not row.get("answer") or not row.get("sources"):
                    continue
                texts = []
                for src in row["sources"]:
                    for m in manuals:
                        if "[" + src + " p" in m:
                            texts += m.split("\n\n")
                            break
                premises = grounding._premises(texts)
                for unit in text_utils.split_units(row["answer"]):
                    if unit in seen or pairs >= max_pairs:
                        continue
                    seen.add(unit)
                    cand = grounding._scoring_premises(unit, premises)
                    if cand:
                        units.append((unit, cand))
                        pairs += len(cand)
    return units, pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--max-pairs", type=int, default=400)
    ap.add_argument("--min-pairs", type=int, default=200)
    ap.add_argument("--threshold", type=float, default=0.35)
    ap.add_argument("--max-gap", type=float, default=0.01)
    a = ap.parse_args()

    import grounding
    units, pairs = build_units(a.runs, a.max_pairs)
    print(f"units: {len(units)}  pairs: {pairs}")

    from sentence_transformers import CrossEncoder
    pt = CrossEncoder(grounding.NLI_MODEL_NAME, revision=grounding.revision(grounding.NLI_MODEL_NAME))
    ox = grounding._OnnxNLI(grounding.NLI_MODEL_NAME)
    col = next(int(i) for i, l in ox.config.id2label.items()
               if str(l).lower().startswith("entail"))
    pcol = next(int(i) for i, l in pt.model.config.id2label.items()
                if str(l).lower().startswith("entail"))
    assert col == pcol, (col, pcol)

    if units:
        u, c = units[0]
        for m in (pt, ox):
            m.predict([(p, u) for p in c], apply_softmax=True)

    t = {"pt": [], "ox": []}
    cpu = {"pt": [], "ox": []}
    flips, max_gap = [], 0.0
    for unit, cand in units:
        pr = [(p, unit) for p in cand]
        res = {}
        for k, m in (("pt", pt), ("ox", ox)):
            w, c0 = time.perf_counter(), time.process_time()
            res[k] = m.predict(pr, apply_softmax=True)
            t[k].append((time.perf_counter() - w) * 1000)
            cpu[k].append((time.process_time() - c0) * 1000)
        a_max, b_max = res["pt"][:, col].max(), res["ox"][:, col].max()
        if (a_max >= a.threshold) != (b_max >= a.threshold):
            flips.append((unit, float(a_max), float(b_max)))
        max_gap = max(max_gap, float(abs(res["pt"] - res["ox"]).max()))

    print(f"verdict flips: {len(flips)}")
    for unit, x, y in flips:
        print(f"  flip: torch {x:.4f} onnx {y:.4f}  {unit[:100]!r}")
    print(f"max score gap: {max_gap:.5f}")
    for k, name in (("pt", "torch"), ("ox", "onnx")):
        if t[k]:
            print(f"{name}: wall median {statistics.median(t[k]):.1f} ms/unit, "
                  f"total {sum(t[k]):.0f} ms, cpu {statistics.mean(cpu[k]):.1f} ms/unit")
    if t["ox"]:
        print(f"speed-up (median wall): "
              f"{statistics.median(t['pt']) / statistics.median(t['ox']):.2f}x")

    bad = []
    if pairs < a.min_pairs:
        bad.append(f"only {pairs} pairs < {a.min_pairs}")
    if flips:
        bad.append(f"{len(flips)} verdict flips")
    if max_gap > a.max_gap:
        bad.append(f"max gap {max_gap:.5f} > {a.max_gap}")
    print("nli_backend_check: " + (f"FAIL ({'; '.join(bad)})" if bad else "PASS"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
