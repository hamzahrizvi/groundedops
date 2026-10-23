"""M8: does the eval grader's verdict change with thinking on, and does it
agree with the person/Claude labels in src/eval_labels.json?

Re-grades the FROZEN answers of one batch (every graded case-run in its
m4_tuned/m4_blind1/m4_blind2 files, deduplicated on question + answer), so
the only thing that varies is the grader call: N runs as eval.py makes it
today (thinking off for DeepSeek) and N inside llm.judging().

  EVAL_GRADER_PROVIDER=deepseek EVAL_GRADER_MODEL=deepseek-v4-flash \
    python tools/grader_ab.py eval_runs/20260928_0807 --repeats 3

Writes <batch>/grader_ab.json and prints the flips and label agreement.
"""
import argparse, ast, glob, json, os, sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
# eval.py reads DEEPSEEK_API_KEY from the environment only (the batch script
# exports it); without this every grade here "returns nothing" and fails.
from dotenv import load_dotenv; load_dotenv(os.path.join(ROOT, "src", ".env"))


def _checks(r):
    c = r.get("checks") or {}
    return c if isinstance(c, dict) else ast.literal_eval(c)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("batch")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    import eval as ev, llm
    refs = {}
    for f in glob.glob(os.path.join(ROOT, "src", "eval_cases*.json")):
        d = json.load(open(f, encoding="utf-8"))
        for c in (d if isinstance(d, list) else d.get("cases", [])):
            if c.get("reference"):
                refs[c["q"]] = c["reference"]

    items = {}
    for name in ("m4_tuned", "m4_blind1", "m4_blind2"):
        path = os.path.join(args.batch, name + ".json")
        if not os.path.exists(path):
            continue
        for r in json.load(open(path, encoding="utf-8"))["results"]:
            ch = _checks(r)
            if "grade" not in ch or r["q"] not in refs:
                continue
            it = items.setdefault((r["q"], r["answer"]), {
                "suite": name, "q": r["q"], "answer": r["answer"],
                "reference": refs[r["q"]], "batch_grades": []})
            it["batch_grades"].append(bool(ch["grade"]))

    labels = {}
    lp = os.path.join(ROOT, "src", "eval_labels.json")
    if os.path.exists(lp):
        for l in json.load(open(lp, encoding="utf-8"))["labels"]:
            labels.setdefault(l["question"], []).append(l)

    def grade(it, think):
        out = []
        for _ in range(args.repeats):
            if think:
                with llm.judging():
                    out.append(ev.llm_grade(it["q"], it["reference"], it["answer"])[0])
            else:
                out.append(ev.llm_grade(it["q"], it["reference"], it["answer"])[0])
        return out

    todo = list(items.values())
    with ThreadPoolExecutor(args.workers) as pool:
        offs = list(pool.map(lambda it: grade(it, False), todo))
        ons = list(pool.map(lambda it: grade(it, True), todo))
    for it, off, on in zip(todo, offs, ons):
        it["off"], it["on"] = off, on
        # The label sheet's answers come from the log (500-char cap), so match
        # on the question and the answer's opening.
        it["label"] = next((l["verdict"] for l in labels.get(it["q"], [])
                            if it["answer"][:80].strip() and
                            l["answer"][:80] == it["answer"][:80]),
                           None)

    n = args.repeats
    unstable = lambda v: 0 < sum(v) < len(v)
    print(f"{len(todo)} frozen answers, grader {ev.GRADER_PROVIDER}/{ev.GRADER_MODEL}, {n}x each way")
    print(f"  pass rate  off {sum(map(sum, offs))}/{n*len(todo)}   on {sum(map(sum, ons))}/{n*len(todo)}")
    print(f"  unstable   off {sum(map(unstable, offs))}   on {sum(map(unstable, ons))}")
    diff = [it for it in todo if (sum(it['off']) * 2 > n) != (sum(it['on']) * 2 > n)]
    print(f"  majority verdict differs off vs on: {len(diff)}")
    for it in diff:
        print(f"    off {sum(it['off'])}/{n} on {sum(it['on'])}/{n}  {it['q'][:70]}")
    lab = [it for it in todo if it["label"] in ("right", "wrong")]
    for mode in ("off", "on"):
        agree = sum((sum(it[mode]) * 2 > n) == (it["label"] == "right") for it in lab)
        print(f"  agrees with label ({mode}): {agree}/{len(lab)}")
    json.dump({"grader": f"{ev.GRADER_PROVIDER}/{ev.GRADER_MODEL}", "repeats": n,
               "items": todo}, open(os.path.join(args.batch, "grader_ab.json"), "w",
                                    encoding="utf-8"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
