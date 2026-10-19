"""Market comparison on the hidden sets (D-20261014-02): this bot vs the baseline vs Kapa/Fin.

  python tools/market_compare.py merge <out_run_dir> <heldout_dir> <ops_runs_dir>
      <out_run_dir> must be under <heldout_dir>/market/ (e.g. OPS/heldout/market/<stamp>); merge refuses
      anywhere else, because runs/ is readable by every Dev lane and the merged set is hidden.
      every <heldout>/2026*/siblings_*.json into <out>/cases.json, deduplicated on (turn1, q),
      minus any question of an open set (<ops_runs>/<YYYYMMDD[a-z]>/cases.json).
  python tools/market_compare.py report <run_dir> [sys_normal,baseline,fin,...]
      counts from <run_dir>/cases_state.json after `blind_eval.py <run_dir> remark`.
  python tools/market_compare.py check <ops_dir>
      counts the files under <ops>/runs that hold a hidden market question (counts only).
  python tools/market_compare.py --selftest

Both print counts only, never a question, reference or answer: the merged set is hidden.
"""
import glob, json, os, re, statistics as st, sys
from collections import Counter

RIGHT, HALF, WRONG, NIM = ("correct", "correct_refusal", "clarified"), ("partial",), ("wrong",), ("safe_decline",)
CITE = re.compile(r"(?i)\b(page|p\.)\s*\d+|\.pdf\b")


def norm(s): return " ".join((s or "").lower().split())


def clamp_refusal(c, g, systems):
    """Only a case expected "rejected" can earn correct_refusal; elsewhere it is a safe_decline worth <= 1."""
    for s in systems:
        if isinstance(g.get(s), dict) and c.get("expected") != "rejected" and g[s].get("verdict") == "correct_refusal":
            g[s] = {**g[s], "verdict": "safe_decline", "score": min(g[s].get("score", 1), 1)}
    return g


def under(path, root):
    p, r = os.path.realpath(path), os.path.realpath(root)
    return p == r or p.startswith(r + os.sep)


def check(ops):
    H = set()
    for f in glob.glob(os.path.join(ops, "heldout", "market", "*", "cases.json")):
        H |= {norm(c["q"]) for c in json.load(open(f, encoding="utf-8"))["cases"]}
    H = {q for q in H if len(q) >= 12}
    n = k = 0
    for d, _, fs in os.walk(os.path.join(ops, "runs")):
        for fn in fs:
            if not fn.endswith((".json", ".jsonl", ".txt")): continue
            n += 1
            try: txt = norm(open(os.path.join(d, fn), encoding="utf-8", errors="replace").read())
            except OSError: continue
            if any(q in txt for q in H): k += 1
    print(json.dumps({"hidden_questions": len(H), "files_scanned": n, "files_holding_one": k}))
    return k


def merge(out, heldout, runs):
    if not under(out, os.path.join(heldout, "market")): sys.exit("hidden cases go under <heldout>/market/<stamp>, not " + out)
    open_q = set()
    for d in glob.glob(os.path.join(runs, "*")):
        f = os.path.join(d, "cases.json")
        if re.fullmatch(r"\d{8}[a-z]?", os.path.basename(d)) and os.path.isfile(f):
            open_q |= {norm(c["q"]) for c in json.load(open(f, encoding="utf-8"))["cases"]}
    files = sorted(glob.glob(os.path.join(heldout, "2026*", "siblings_*.json")))
    seen, ids, kept, n, dup, dropped = set(), set(), [], 0, 0, 0
    for f in files:
        j = json.load(open(f, encoding="utf-8"))
        src = f"{os.path.basename(os.path.dirname(f))}/{os.path.splitext(os.path.basename(f))[0]}"
        for c in (j["cases"] if isinstance(j, dict) else j):
            n += 1
            key = (norm(c.get("turn1")), norm(c["q"]))
            if key in seen: dup += 1; continue
            if norm(c["q"]) in open_q: dropped += 1; continue
            seen.add(key)
            c = {**c, "src": src}
            if c["id"] in ids: c["id"] = f"{src}:{c['id']}"
            ids.add(c["id"]); kept.append(c)
    os.makedirs(out, exist_ok=True)
    json.dump({"cases": kept}, open(os.path.join(out, "cases.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    counts = {"files": len(files), "read": n, "duplicates": dup, "open_set_dropped": dropped, "written": len(kept),
              "by_type": dict(Counter(c.get("type") for c in kept)), "by_product": dict(Counter(str(c.get("product")) for c in kept))}
    print(json.dumps(counts, indent=1))
    return counts


def bucket(verdict):
    for name, vs in (("right", RIGHT), ("half", HALF), ("wrong", WRONG), ("not_in_manual", NIM)):
        if verdict in vs: return name
    return "other"


def report(run, systems=None):
    """Marks and verdicts as the office page reads a run (tools/ops_pivot.py run_facts, D-20261019-06): with
    cases_state.r2.json ... beside it, each question's mean mark over the answers, "wrong" when most are.
    Times and citations are repeat 1's answers."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ops_pivot import run_facts           # here, not at the top: blind_eval.py imports this module
    from retrofit_gate import repeat_paths
    cases = json.load(open(os.path.join(run, "cases.json"), encoding="utf-8"))["cases"]
    state = json.load(open(os.path.join(run, "cases_state.json"), encoding="utf-8"))
    grades = {k: g for k, g in state.get("grades", {}).items() if "raw" not in g}
    systems = systems or ["sys_normal", "baseline"]
    facts = {s: {f["id"]: f for f in run_facts(run, s)} for s in systems}
    ok = [c for c in cases if c["id"] in grades and all(s in grades[c["id"]] and c["id"] in facts[s] for s in systems)]
    n = len(repeat_paths(os.path.join(run, "cases_state.json")))
    out = {"cases": len(cases), "graded": len(ok), "answers": "one answer" if n == 1 else f"mean of {n} answers", "systems": {}}
    for s in systems:
        rows, fs = state.get(s, {}), facts[s]
        tally = lambda cs: dict(Counter(bucket(fs[c["id"]]["verdict"]) for c in cs))
        walls = [rows[c["id"]]["wall"] for c in ok if c["id"] in rows]
        ans = [rows[c["id"]].get("answer", "") for c in ok if c["id"] in rows]
        r = {"answered": sum(1 for c in cases if c["id"] in rows), "no_answer": sum(1 for c in cases if c["id"] not in rows),
             "overall": tally(ok), "by_type": {t: tally([c for c in ok if c["type"] == t]) for t in sorted({c["type"] for c in ok})},
             "pct_of_max": round(100 * sum(fs[c["id"]]["bot"] for c in ok) / (3 * len(ok)), 1) if ok else None,
             "median_s": st.median(walls) if walls else None,
             "citation_rate": round(sum(1 for a in ans if CITE.search(a or "")) / len(ans), 2) if ans else None}
        if s != "sys_normal" and "sys_normal" in systems and len(ok) > 1:
            d = [fs[c["id"]]["bot"] - facts["sys_normal"][c["id"]]["bot"] for c in ok]
            r["gap_vs_sys_normal"] = {"points": round(100 * st.mean(d) / 3, 1),
                                      "band95": round(1.96 * 100 * st.stdev(d) / (3 * len(d) ** .5), 1), "n": len(d)}
        out["systems"][s] = r
    json.dump(out, open(os.path.join(run, "market_report.json"), "w", encoding="utf-8"), indent=1)
    print(json.dumps(out, indent=1))
    return out


def selftest():
    import tempfile, contextlib, io
    with tempfile.TemporaryDirectory() as t, contextlib.redirect_stdout(io.StringIO()):
        def w(p, obj):
            os.makedirs(os.path.dirname(p), exist_ok=True); json.dump(obj, open(p, "w", encoding="utf-8"))
        cs = lambda *qs: {"cases": [{"id": i, "q": q, "type": "lookup", "expected": "answered"} for i, q in qs]}
        w(f"{t}/held/20261001/siblings_a.json", cs(("a1", "How hot?"), ("a2", "Open one"), ("a3", "Third")))
        w(f"{t}/held/20261002/siblings_b.json", cs(("a1", "how  HOT?"), ("a3", "New one"), ("b2", "Other"), ("b3", "Opener")))
        w(f"{t}/held/gates/D-1.gate/siblings_x.json", cs(("x", "Gate only")))
        w(f"{t}/runs/20261013/cases.json", cs(("o1", "open one")))
        w(f"{t}/runs/20261013-market/cases.json", cs(("o2", "Other")))   # not an open set: name has a suffix
        try: merge(f"{t}/out", f"{t}/held", f"{t}/runs"); raise AssertionError("merge wrote outside heldout/market")
        except SystemExit: pass
        n = merge(f"{t}/held/market/out", f"{t}/held", f"{t}/runs")
        assert (n["files"], n["read"], n["duplicates"], n["open_set_dropped"], n["written"]) == (2, 7, 1, 1, 5), n
        got = [c["id"] for c in json.load(open(f"{t}/held/market/out/cases.json", encoding="utf-8"))["cases"]]
        assert got == ["a1", "a3", "20261002/siblings_b:a3", "b2", "b3"], got

        w(f"{t}/heldout/market/m1/cases.json", cs(("m", "How hot does the unit get?")))   # check() reads <ops>/heldout/market
        assert check(t) == 0
        w(f"{t}/runs/20261015/cases_state.json", {"x": "asked: how hot does the  unit get? ok"})
        assert check(t) == 1
        os.remove(f"{t}/runs/20261015/cases_state.json")
        assert check(t) == 0

        sc = lambda v, s: {"verdict": v, "score": s}
        w(f"{t}/r/cases.json", {"cases": [{"id": i, "type": ty} for i, ty in (("c1", "x"), ("c2", "x"), ("c3", "y"))]})
        w(f"{t}/r/cases_state.json", {
            "sys_normal": {i: {"answer": "see page 4", "wall": 2} for i in ("c1", "c2", "c3")},
            "fin": {"c1": {"answer": "yes", "wall": 1}, "c2": {"answer": "manual.pdf", "wall": 3}},
            "grades": {"c1": {"sys_normal": sc("correct", 3), "fin": sc("safe_decline", 1)},
                       "c2": {"sys_normal": sc("partial", 2), "fin": sc("partial", 2)}}})
        r = report(f"{t}/r", ["sys_normal", "fin"])
        assert r["graded"] == 2, r
        f = r["systems"]["fin"]
        assert f["answered"] == 2 and f["no_answer"] == 1 and f["overall"] == {"not_in_manual": 1, "half": 1}, f
        assert f["citation_rate"] == .5 and f["median_s"] == 2, f
        assert f["gap_vs_sys_normal"]["points"] == -33.3, f   # d = [-2, 0]
        assert r["systems"]["sys_normal"]["by_type"] == {"x": {"right": 1, "half": 1}}, r
        assert r["answers"] == "one answer", r
        # D-20261019-06: two more answers; c1's fin is wrong in 2 of 3 (wrong), c2's sys_normal in 1 of 3 (not)
        for k, (fin1, sn2) in ((2, (sc("wrong", 0), sc("wrong", 0))), (3, (sc("wrong", 0), sc("correct", 3)))):
            w(f"{t}/r/cases_state.r{k}.json", {"grades": {"c1": {"sys_normal": sc("correct", 3), "fin": fin1},
                                                          "c2": {"sys_normal": sn2, "fin": sc("partial", 2)}}})
        r = report(f"{t}/r", ["sys_normal", "fin"])
        assert r["answers"] == "mean of 3 answers" and r["graded"] == 2, r
        f, n = r["systems"]["fin"], r["systems"]["sys_normal"]
        assert f["overall"] == {"wrong": 1, "half": 1} and f["pct_of_max"] == round(100 * (1 / 3 + 2) / 6, 1), f
        assert n["overall"] == {"right": 1, "half": 1} and n["pct_of_max"] == round(100 * (3 + 5 / 3) / 6, 1), n
        assert f["gap_vs_sys_normal"]["points"] == round(100 * ((1 / 3 - 3) + (2 - 5 / 3)) / 2 / 3, 1), f
        assert f["median_s"] == 2, f                                                     # times: repeat 1's answers
    g = clamp_refusal({"expected": "answered"}, {"sys_normal": sc("correct_refusal", 3)}, ["sys_normal"])
    assert g["sys_normal"] == sc("safe_decline", 1), g
    g = clamp_refusal({"expected": "rejected"}, {"sys_normal": sc("correct_refusal", 3)}, ["sys_normal"])
    assert g["sys_normal"] == sc("correct_refusal", 3), g
    print("selftest ok")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["--selftest"]: selftest()
    elif a[:1] == ["merge"] and len(a) == 4: merge(*a[1:])
    elif a[:1] == ["check"] and len(a) == 2: check(a[1])
    elif a[:1] == ["report"] and len(a) in (2, 3): report(a[1], a[2].split(",") if len(a) == 3 else None)
    else: sys.exit(__doc__)
