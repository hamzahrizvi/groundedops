"""Retrofit gate: does a fix generalise, or does it only fix the question that prompted it?

  python tools/retrofit_gate.py --before A_state.json --after B_state.json \
      [--siblings S_state.json ...] [--system sys_normal] [--noise 3]
  python tools/retrofit_gate.py --leakcheck DIR_OR_FILE... --cases SIBLINGS.json...
  python tools/retrofit_gate.py --selftest

Reads blind_eval.py state files (0-3 grades per case). PASS needs all three:
  1. siblings: every hidden sibling question (same failure type, never shown
     to Dev) scores >= 2 on the new code;
  2. no case that scored >= 2 before scores 0 (stated something wrong) after;
  3. the overall score does not drop by more than --noise percentage points.
A single blind pass is noisy: on FAIL from rule 2 alone, re-run the flipped
cases before ruling. Prints JSON; exit 0 PASS, 1 FAIL.

--leakcheck: does any file under the paths hold text of a hidden question?
Prints case ids and file names only (never the text); exit 1 on a hit.
"""
import argparse
import json
import os
import re
import sys


def scores(state, system):
    return {cid: g[system]["score"] for cid, g in state.get("grades", {}).items() if system in g}


def pct(sc):
    return round(100 * sum(sc.values()) / (3 * len(sc)), 1) if sc else 0.0


def gate(before, after, siblings=(), system="sys_normal", noise=3.0):
    b, a = scores(before, system), scores(after, system)
    common = sorted(set(b) & set(a))
    became_wrong = [cid for cid in common if b[cid] >= 2 and a[cid] == 0]
    fixed = [cid for cid in common if b[cid] <= 1 and a[cid] >= 2]
    drop = round(pct({c: b[c] for c in common}) - pct({c: a[c] for c in common}), 1)
    sib_fail = [cid for s in siblings for cid, v in scores(s, system).items() if v < 2]
    reasons = ([f"hidden siblings below 2: {sib_fail}"] if sib_fail else []) + \
              ([f"became wrong: {became_wrong}"] if became_wrong else []) + \
              ([f"score dropped {drop} pts (> {noise})"] if drop > noise else [])
    if siblings and not any(scores(s, system) for s in siblings):
        reasons.append("sibling files have no grades for " + system)
    return {"verdict": "FAIL" if reasons else "PASS", "reasons": reasons,
            "before_pct": pct({c: b[c] for c in common}), "after_pct": pct({c: a[c] for c in common}),
            "fixed": fixed, "became_wrong": became_wrong, "cases": len(common),
            "siblings": sum(len(scores(s, system)) for s in siblings)}


def _needle(text):
    # ponytail: one needle per question = first 40 chars of its longest run without quotes,
    # backslashes or non-ASCII (so JSON escaping and repr() in logs still match). A question whose
    # every run is under 15 chars is not checked; the folder split in gate_run.sh is the guarantee.
    run = max(re.split(r"[\"'\\\r\n]|[^\x20-\x7e]", text or ""), key=len).strip()
    return run[:40] if len(run) >= 15 else None


def leaks(cases, paths):
    """[(case id, file)] for every file under paths that contains a case's q or turn1."""
    needles = [(c["id"], n) for c in cases for n in map(_needle, (c.get("q"), c.get("turn1"))) if n]
    files = [os.path.join(r, f) for p in paths for r, _, fs in os.walk(p) for f in fs] + \
            [p for p in paths if os.path.isfile(p)]
    hits = []
    for f in files:
        text = open(f, encoding="utf-8", errors="ignore").read()
        hits += [(cid, f) for cid, n in needles if n in text and (cid, f) not in hits]
    return hits


def _selftest():
    st = lambda d: {"grades": {k: {"sys_normal": {"score": v}} for k, v in d.items()}}
    before = st({"a": 3, "b": 1, "c": 2, "d": 3})
    assert gate(before, st({"a": 3, "b": 3, "c": 2, "d": 3}), [st({"s1": 3, "s2": 2})])["verdict"] == "PASS"
    r = gate(before, st({"a": 3, "b": 3, "c": 2, "d": 3}), [st({"s1": 3, "s2": 1})])
    assert r["verdict"] == "FAIL" and "s2" in r["reasons"][0], r     # retrofit: only the seen case fixed
    r = gate(before, st({"a": 3, "b": 3, "c": 0, "d": 3}), [st({"s1": 3})])
    assert r["verdict"] == "FAIL" and r["became_wrong"] == ["c"], r  # regression
    r = gate(before, st({"a": 2, "b": 1, "c": 2, "d": 2}), [st({"s1": 3})], noise=3)
    assert r["verdict"] == "FAIL" and "dropped" in r["reasons"][0], r
    import tempfile
    sibs = [{"id": "h1", "q": 'What does the "amber" light on the reader mean?'},
            {"id": "h2", "turn1": "Which models accept polymer notes?", "q": "and that one?"}]
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "clean.log"), "w").write("INFO q='How do I clear a jam in the stacker?'\n")
        assert leaks(sibs, [d]) == [], leaks(sibs, [d])                      # open-set text only
        os.makedirs(os.path.join(d, "sub"))
        bad = os.path.join(d, "sub", "app.log")
        open(bad, "w").write(json.dumps({"q": sibs[0]["q"]}) + "\nINFO 'Which models accept polymer notes?'")
        assert leaks(sibs, [d]) == [("h1", bad), ("h2", bad)], leaks(sibs, [d])  # escaped + repr both hit
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest(); sys.exit(0)
    if "--leakcheck" in sys.argv:
        ap = argparse.ArgumentParser()
        ap.add_argument("--leakcheck", nargs="+", required=True)
        ap.add_argument("--cases", nargs="+", required=True)
        o = ap.parse_args()
        cs = [c for p in o.cases for c in json.load(open(p, encoding="utf-8"))["cases"]]
        hits = leaks(cs, o.leakcheck)
        print("; ".join(f"{cid} in {f}" for cid, f in hits))
        sys.exit(1 if hits else 0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--siblings", nargs="*", default=[])
    ap.add_argument("--system", default="sys_normal")
    ap.add_argument("--noise", type=float, default=3.0)
    o = ap.parse_args()
    load = lambda p: json.load(open(p, encoding="utf-8"))
    res = gate(load(o.before), load(o.after), [load(p) for p in o.siblings], o.system, o.noise)
    print(json.dumps(res, indent=1))
    sys.exit(0 if res["verdict"] == "PASS" else 1)
