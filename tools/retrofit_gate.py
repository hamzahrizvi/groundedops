"""Retrofit gate: does a fix generalise, or does it only fix the question that prompted it?

  python tools/retrofit_gate.py --before A_state.json --after B_state.json \
      [--siblings S_state.json ...] [--system sys_normal] [--noise 3]
  python tools/retrofit_gate.py --selftest

Reads blind_eval.py state files (0-3 grades per case). PASS needs all three:
  1. siblings: every hidden sibling question (same failure type, never shown
     to Dev) scores >= 2 on the new code;
  2. no case that scored >= 2 before scores 0 (stated something wrong) after;
  3. the overall score does not drop by more than --noise percentage points.
A single blind pass is noisy: on FAIL from rule 2 alone, re-run the flipped
cases before ruling. Prints JSON; exit 0 PASS, 1 FAIL.
"""
import argparse
import json
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
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest(); sys.exit(0)
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
