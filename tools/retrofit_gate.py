"""Retrofit gate: does a fix generalise, or does it only fix the question that prompted it?

  python tools/retrofit_gate.py --before A_state.json --after B_state.json \
      [--siblings S_state.json ...] [--system sys_normal] [--noise 3] [--regraded sys_before] [--models M.json] [--manuals DIR]
      [--rule r1|mean]
  python tools/retrofit_gate.py --leakcheck DIR_OR_FILE... --cases SIBLINGS.json...
  python tools/retrofit_gate.py --selftest

Reads blind_eval.py state files (0-3 grades per case). PASS needs all three:
  1. siblings: no hidden sibling question (same failure type, never shown to Dev) scores under 2 on the
     new code where its base code scored 2+ (sys_before in its state file, marked in the same pass; no
     base mark counts as 2+). Under 2 on both sides is listed as siblings_unmoved and passes; raised to
     2+ is listed as siblings_fixed;
  2. no case that scored >= 2 before is wrong after (0 marks, or the verdict "wrong" at any mark);
  3. the overall score does not drop by more than --noise percentage points.
Rule 3 and rule 2 use the before answers re-graded in the same pass when --regraded is
given (gate.json keeps before_pct_stored so grader drift shows); tools/gate_rerun.py re-asks
1-2 flipped cases when those flips are the only reason, or the flips plus a drop that is
within noise once they are held at their before scores. Prints JSON; exit 0 PASS, 1 FAIL.

Repeats (D-20261018-02): X_state.r2.json, .r3.json beside X_state.json are more answers to the same
questions (blind_eval.py --repeats). When any file given has them, the gate is also read on all of
them as "mean_of_repeats": marks are per-question means, "became wrong" needs a wrong answer in more
than half the repeats, a sibling fails on its means. The top-level verdict is the one-answer (r1) gate
unless --rule mean, which swaps them (the r1 reading is then kept under "r1"). "paired" is the mean
per-question after-minus-before difference in points with its 90% interval (t, questions paired).

--leakcheck: does any file under the paths hold text of a hidden question?
Prints case ids and file names only (never the text); exit 1 on a hit.
"""
import argparse
import glob
import json
import os
import re
import sys


def repeat_paths(p):
    """[X_state.json, X_state.r2.json, ...]: the repeats of a run's state that exist, in repeat order."""
    stem = p[:-5] if p.endswith(".json") else p
    rs = sorted((int(m.group(1)), f) for f in glob.glob(glob.escape(stem) + ".r*.json")
                if (m := re.search(r"\.r(\d+)\.json$", f)))
    return ([p] if os.path.exists(p) else []) + [f for _, f in rs]


def _states(x):
    return x if isinstance(x, list) else [x]


def scores(state, system):
    """case -> mark; a list of repeat states gives each case's mean over the repeats that have it."""
    per = {}
    for s in _states(state):
        for cid, g in s.get("grades", {}).items():
            if system in g:
                per.setdefault(cid, []).append(g[system]["score"])
    return {cid: v[0] if len(v) == 1 else sum(v) / len(v) for cid, v in per.items()}


def paired(b, a):
    """Mean per-question after-minus-before difference in points (x100/3) with its 90% interval."""
    d = [100 * (a[c] - b[c]) / 3 for c in sorted(set(a) & set(b))]
    if len(d) < 2:
        return {"diff": round(d[0], 1) if d else None, "lo": None, "hi": None, "n": len(d)}
    from scipy.stats import t
    m = sum(d) / len(d)
    half = t.ppf(0.95, len(d) - 1) * (sum((x - m) ** 2 for x in d) / (len(d) - 1)) ** 0.5 / len(d) ** 0.5
    return {"diff": round(m, 1), "lo": round(m - half, 1), "hi": round(m + half, 1), "n": len(d)}


def pct(sc):
    return round(100 * sum(sc.values()) / (3 * len(sc)), 1) if sc else 0.0


def wrong(g):
    """The marker called this answer wrong: 0 marks, or verdict "wrong" at any mark (D-20261010-11)."""
    return g.get("score") == 0 or g.get("verdict") == "wrong"


def gate(before, after, siblings=(), system="sys_normal", noise=3.0, regraded=None):
    """before/after: a state or a list of repeat states; siblings: each one a state or a list of repeats."""
    stored = scores(before, system)
    b = (scores(after, regraded) if regraded else {}) or stored
    a = scores(after, system)
    common = sorted(set(b) & set(a))
    nwrong, nrep = {}, {}   # per case: repeats marked wrong, repeats graded
    for s in _states(after):
        for cid, g in s.get("grades", {}).items():
            if system in g:
                nwrong[cid] = nwrong.get(cid, 0) + wrong(g[system]); nrep[cid] = nrep.get(cid, 0) + 1
    became_wrong = [cid for cid in common if b[cid] >= 2 and nwrong[cid] > nrep[cid] / 2]
    fixed = [cid for cid in common if b[cid] <= 1 and a[cid] >= 2]
    drop = round(pct({c: b[c] for c in common}) - pct({c: a[c] for c in common}), 1)
    sib_after = {cid: v for s in siblings for cid, v in scores(s, system).items()}
    sib_before = {cid: v for s in siblings for cid, v in scores(s, "sys_before").items()}
    # D-20261016-10: a sibling fails only when it is under 2 on the branch and 2+ on base; no base mark counts as 2+
    sib_fail = [c for c, v in sib_after.items() if v < 2 and sib_before.get(c, 2) >= 2]
    unmoved = [c for c, v in sib_after.items() if v < 2 and sib_before.get(c, 2) < 2]
    sib_fixed = [c for c, v in sib_after.items() if v >= 2 and sib_before.get(c, 2) < 2]
    reasons = ([f"hidden siblings below 2 on the branch, 2+ on base: {sib_fail}"] if sib_fail else []) + \
              ([f"became wrong: {became_wrong}"] if became_wrong else []) + \
              ([f"score dropped {drop} pts (> {noise})"] if drop > noise else [])
    if siblings and not any(scores(s, system) for s in siblings):
        reasons.append("sibling files have no grades for " + system)
    return {"verdict": "FAIL" if reasons else "PASS", "reasons": reasons,
            "before_pct": pct({c: b[c] for c in common}), "after_pct": pct({c: a[c] for c in common}),
            "before_pct_stored": pct({c: stored[c] for c in common if c in stored}),
            "before_regraded": b is not stored, "drop": drop, "noise": noise,
            "flip_before": {c: b[c] for c in became_wrong},
            "flip_after": {c: a[c] for c in became_wrong},
            "fixed": fixed, "became_wrong": became_wrong, "cases": len(common),
            "paired": paired({c: b[c] for c in common}, {c: a[c] for c in common}),
            "repeats": len(_states(after)),
            "siblings": sum(len(scores(s, system)) for s in siblings),
            "sibling_marks": {c: {"before": sib_before.get(c), "after": v} for c, v in sib_after.items()},
            "siblings_fixed": sib_fixed, "siblings_unmoved": unmoved}


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
    assert r["flip_before"] == {"c": 2} and r["drop"] == 0.0, r
    r = gate(before, st({"a": 2, "b": 1, "c": 2, "d": 2}), [st({"s1": 3})], noise=3)
    assert r["verdict"] == "FAIL" and "dropped" in r["reasons"][0], r
    s0 = st({"a": 3, "b": 1})
    aft = {"grades": {"a": {"sys_normal": {"score": 0}, "sys_before": {"score": 1}},
                      "b": {"sys_normal": {"score": 1}, "sys_before": {"score": 1}}}}
    r = gate(s0, aft, regraded="sys_before")
    assert r["became_wrong"] == [] and r["before_pct"] == 33.3 and r["before_pct_stored"] == 66.7, r
    assert r["before_regraded"] is True, r
    r = gate(s0, aft)
    assert r["became_wrong"] == ["a"] and r["before_regraded"] is False, r
    r = gate(st({"a": 3, "b": 3}), {"grades": {"a": {"sys_normal": {"score": 1, "verdict": "wrong"}},
                                               "b": {"sys_normal": {"score": 1, "verdict": "safe_decline"}}}})
    assert r["became_wrong"] == ["a"] and r["flip_after"] == {"a": 1}, r   # D-20261010-11: "wrong" with 1 mark
    st2 = lambda d: {"grades": {k: {"sys_normal": {"score": a}, "sys_before": {"score": b}} for k, (b, a) in d.items()}}
    fixed_open = st({"a": 3, "b": 3, "c": 2, "d": 3})
    r = gate(before, fixed_open, [st2({"u": (1, 1), "f": (1, 3), "k": (3, 3)})])
    assert r["verdict"] == "PASS" and r["siblings_unmoved"] == ["u"] and r["siblings_fixed"] == ["f"], r
    assert r["sibling_marks"] == {"u": {"before": 1, "after": 1}, "f": {"before": 1, "after": 3}, "k": {"before": 3, "after": 3}}, r
    r = gate(before, fixed_open, [st2({"u": (1, 1), "x": (2, 1)})])
    assert r["verdict"] == "FAIL" and r["reasons"] == ["hidden siblings below 2 on the branch, 2+ on base: ['x']"], r
    # D-20261018-02: three repeats. r1 alone fails on one flip; wrong in 1 of 3 passes on the mean, 2 of 3 fails
    rp = lambda *ds: [st(d) for d in ds]
    b3 = rp({"a": 3, "c": 3}, {"a": 3, "c": 3}, {"a": 3, "c": 2})
    assert gate(b3[0], st({"a": 0, "c": 3}))["verdict"] == "FAIL"
    r = gate(b3, rp({"a": 0, "c": 3}, {"a": 3, "c": 3}, {"a": 3, "c": 3}), noise=40)
    assert r["verdict"] == "PASS" and r["became_wrong"] == [] and r["repeats"] == 3, r
    r = gate(b3, rp({"a": 0, "c": 3}, {"a": 0, "c": 3}, {"a": 3, "c": 3}), noise=40)
    assert r["verdict"] == "FAIL" and r["became_wrong"] == ["a"] and r["flip_after"] == {"a": 1.0}, r
    sib3 = [st2({"x": (2, 3)}), st2({"x": (2, 1)}), st2({"x": (2, 1)})]       # after mean 1.67 < 2, base 2
    r = gate(b3, b3, [sib3])
    assert r["verdict"] == "FAIL" and r["sibling_marks"]["x"]["after"] == 5 / 3, r
    assert gate(b3[0], b3[0], [sib3[0]])["verdict"] == "PASS"                 # r1's sibling answer alone is 3
    p = paired({"a": 3, "b": 2, "c": 1}, {"a": 3, "b": 3, "c": 1})
    assert p["diff"] == 11.1 and p["lo"] < 0 < p["hi"] and p["n"] == 3, p
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        for f in ("cases_state.json", "cases_state.r10.json", "cases_state.r2.json", "cases_state.graded-by-gpt-5.json"):
            open(os.path.join(d, f), "w").write("{}")
        assert [os.path.basename(f) for f in repeat_paths(os.path.join(d, "cases_state.json"))] ==             ["cases_state.json", "cases_state.r2.json", "cases_state.r10.json"]
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
    ap.add_argument("--regraded", default=None,
                    help="system key in --after holding the before answers re-graded in the same pass, e.g. sys_before")
    ap.add_argument("--models")   # blind_eval.py <run> models output (D-20261010-09)
    ap.add_argument("--manuals")   # the manuals/ the one marking pass read (D-20261010-23)
    ap.add_argument("--rule", choices=("r1", "mean"), default="r1")   # which reading sets the verdict (D-20261018-02)
    o = ap.parse_args()
    load = lambda p: json.load(open(p, encoding="utf-8"))
    reps = lambda p: [load(f) for f in repeat_paths(p)]
    B, A, S = reps(o.before), reps(o.after), [reps(p) for p in o.siblings]
    res = gate(B[0], A[0], [s[0] for s in S], o.system, o.noise, o.regraded)
    if max([len(B), len(A)] + [len(s) for s in S]) > 1:
        mean = gate(B, A, S, o.system, o.noise, o.regraded)
        res = {**mean, "r1": res} if o.rule == "mean" else {**res, "mean_of_repeats": mean}
    if o.models:   # which before answers the gate marked, and both bot models
        res["models"] = load(o.models)
        res["before"] = "stored" if res["models"]["same"] else "re-asked"
    if o.manuals is not None:   # before and after are marked in one pass, so with the same pages
        res["manuals"] = sorted(os.listdir(o.manuals)) if os.path.isdir(o.manuals) else []
    print(json.dumps(res, indent=1))
    sys.exit(0 if res["verdict"] == "PASS" else 1)
