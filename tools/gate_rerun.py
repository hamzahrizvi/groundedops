"""Gate re-run: a one- or two-case "became wrong" is re-asked before it fails a branch.

  python tools/gate_rerun.py <dev_worktree> <run_dir> <out_dir>
  python tools/gate_rerun.py --selftest

tools/gate_run.sh calls it after tools/retrofit_gate.py has written <out_dir>/gate.json.
One blind pass is noisy: C20261008-22 scored 0 in 2 passes of 6 on unchanged code
(2026-10-08). So when the only reasons for FAIL are "became wrong" on 1-2 cases, alone or
with a score drop that is under --noise once those cases are held at their before scores
(D-20261010-05: one 3 -> 0 flip is 2.5 pts of 40 cases), each of
those cases is asked 6 times on the branch and 6 times on its base code, interleaved.
A case stays wrong only if the branch answers it wrong (0 marks or verdict "wrong") at least 2 passes more
often than the base, or a pass has no grade (fail closed). gate.json gains "rerun" (the
per-pass grades) and a recomputed verdict; the drop is recomputed with the cleared cases
held before ruling. Siblings, a drop at or beyond noise, 3+ cases or a FAIL on the paired interval
(D-20261019-06: it cannot be rebuilt from gate.json, so it fails closed): untouched.
Only the open-set ids in gate.json's "became_wrong" are re-run; siblings are never read.
A gate run with REPEATS>1 (D-20261018-02) already holds that many passes per case (after/cases_state.r<k>.json,
the base side as their sys_before; each sibling's repeat files): they are the first passes, and only ROUNDS
minus that many more are asked. A one-answer gate asks all 6 open-set passes afresh, as before.

Env: GO_DATA (as gate_run.sh exports it); GATE_BASE = base commit (default: merge-base
of the branch and GO_DATA's checkout). A failed step keeps the single-pass FAIL and
records {"rerun": {"error": <command>}}; exit 0 either way.
"""
import json
import os
import shutil
import subprocess
import sys
import tarfile

from retrofit_gate import wrong, repeat_paths, scores   # one rule for "wrong" (D-20261010-11)

ROUNDS, MARGIN, MAX_CASES = 6, 2, 2
MAX_SIBS = 3   # the CEO, 2026-10-14: a hidden sibling under 2 is re-asked before it fails a fix; more than this many is a real miss


def settle(passes, missing=0):
    """A re-asked sibling's mark: the lower median of its passes, so it clears 2 only when most passes do
    (4 of 6). A pass without a grade (None) counts against the fix: 0 on the branch, missing=3 on the base
    side: fail closed."""
    v = sorted(missing if x is None else x for x in passes)
    return v[(len(v) - 1) // 2]


def sibling_rerun(held, states, dev_src, data, base_src):
    """Re-ask each hidden sibling the fix would fail (under 2 on the branch, 2+ on base) ROUNDS-1 more times on
    the branch and on its base code, in <held> (never where Dev reads), and write the settled marks back into its
    sib_*/cases_state.json as sys_normal / sys_before. Too many such siblings, or any failed step: the single-pass
    marks stand. gate_run.sh calls it as --siblings <held> <dev> <base_code> <state.json...>."""
    here = os.path.dirname(os.path.abspath(__file__))
    be = os.environ.get("BE") or os.path.join(here, "blind_eval.py")   # gate_run.sh's BE (a test's fake lab)
    reps = {sp: [json.load(open(f, encoding="utf-8")) for f in repeat_paths(sp)] for sp in states}
    def under(ss):   # under 2 on the branch, 2+ on base (no base mark counts as 2+)
        a, b = scores(ss, "sys_normal"), scores(ss, "sys_before")
        return {c for c, v in a.items() if v < 2 and b.get(c, 3) >= 2}
    # low on the one-answer reading or on the mean of the repeats: both readings then see the settled mark
    low = {sp: sorted(under(rs[:1]) | under(rs)) for sp, rs in reps.items()}
    n = sum(len(v) for v in low.values())
    out = {"asked": n, "rounds": ROUNDS, "cleared": [], "kept": []}
    if not n or n > MAX_SIBS:
        out["skipped"] = "none under 2 on the branch and 2+ on base" if not n else f"{n} under 2, more than {MAX_SIBS}: a real miss"
        return out
    rr = os.path.join(held, "sib_rerun")
    shutil.rmtree(rr, ignore_errors=True)
    log = open(os.path.join(held, "sib_rerun.log"), "a", encoding="utf-8")
    try:
        for sp, ids in low.items():
            if not ids:
                continue
            name = os.path.basename(os.path.dirname(sp))
            cases = [c for c in json.load(open(os.path.join(os.path.dirname(sp), "cases.json"), encoding="utf-8"))["cases"] if c["id"] in ids]
            rs = reps[sp]
            sides = {"sys_normal": dev_src, "sys_before": base_src}
            passes = {k: {c: [((x.get("grades", {}).get(c) or {}).get(k) or {}).get("score") for x in rs] for c in ids} for k in sides}
            for r in range(len(rs) + 1, ROUNDS + 1):
                for k, src in sides.items():   # interleaved: branch, then base, each round
                    d = os.path.join(rr, name, k, f"r{r}")
                    os.makedirs(d)
                    json.dump({"cases": cases}, open(os.path.join(d, "cases.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
                    env = {**os.environ, "GO_CODE": src, "GO_DATA": data, "SYSTEMS": "sys_normal"}
                    for cmd in ([sys.executable, "-X", "faulthandler", be, d, "system", "normal"], [sys.executable, be, d, "grade"]):
                        log.write(f"== {d}: {' '.join(cmd[-2:])}\n"); log.flush()
                        if subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT).returncode:
                            raise RuntimeError(" ".join(cmd))
                    got = json.load(open(os.path.join(d, "cases_state.json"), encoding="utf-8"))
                    for c in ids:
                        g = grade_of(got, c)
                        passes[k][c].append(g["score"] if g else None)
            for c in ids:
                after, before = settle(passes["sys_normal"][c]), settle(passes["sys_before"][c], missing=3)
                for i, x in enumerate(rs):   # every repeat carries the settled mark, so r1 and the mean agree
                    for k, mark in (("sys_normal", after), ("sys_before", before)):
                        x.setdefault("grades", {}).setdefault(c, {}).setdefault(k, {}).update(
                            {"score": mark, "passes": passes[k][c], "single_pass": passes[k][c][i]})
                (out["kept"] if after < 2 and before >= 2 else out["cleared"]).append(c)
            for f, x in zip(repeat_paths(sp), rs):
                json.dump(x, open(f, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    except Exception as e:   # the single-pass marks stand
        out["error"] = str(e)
    finally:
        log.close()
    return out


def held_drop(gate, cids):
    """The score drop with these became-wrong cases held at their before scores."""
    return round(gate["drop"] - 100 * sum(gate["flip_before"][c] - gate.get("flip_after", {}).get(c, 0) for c in cids) / (3 * gate["cases"]), 1)


def eligible(gate):
    r, bw = gate.get("reasons", []), gate.get("became_wrong", [])
    if not (gate.get("verdict") == "FAIL" and 1 <= len(bw) <= MAX_CASES
            and all(x.startswith(("became wrong", "score dropped")) for x in r)
            and any(x.startswith("became wrong") for x in r)
            and not any("interval" in x for x in r)):
        return False
    if any(x.startswith("score dropped") for x in r):
        return ("drop" in gate and all(c in gate.get("flip_before", {}) for c in bw)
                and held_drop(gate, bw) < gate.get("noise", 4.0))
    return True


def grade_of(state, cid):
    g = state.get("grades", {}).get(cid, {}).get("sys_normal")
    return g if isinstance(g, dict) and "score" in g else None


def rerate(gate, head_states, base_states):
    rerun, kept = {}, []
    show = lambda g: f'{g["score"]} {g.get("verdict", "")}'.strip() if g else "no grade"
    for cid in gate["became_wrong"]:
        h = [grade_of(s, cid) for s in head_states]
        b = [grade_of(s, cid) for s in base_states]
        hw = sum(1 for g in h if g and wrong(g))
        bw = sum(1 for g in b if g and wrong(g))
        keep = not h or not b or None in h + b or hw - bw >= MARGIN
        rerun[cid] = {"head": [show(g) for g in h], "base": [show(g) for g in b],
                      "head_wrong": hw, "base_wrong": bw, "kept": keep}
        if keep:
            kept.append(cid)
    rates = ", ".join(f"{c} wrong {rerun[c]['head_wrong']}/{len(rerun[c]['head'])} on branch vs "
                      f"{rerun[c]['base_wrong']}/{len(rerun[c]['base'])} on base" for c in kept)
    reasons = [f"became wrong: {kept} (re-run: {rates})"] if kept else []
    extra = {}
    if "drop" in gate:
        cleared, noise = [c for c in gate["became_wrong"] if c not in kept], gate.get("noise", 4.0)
        d = extra["drop_held"] = held_drop(gate, cleared)
        if d >= noise:
            reasons.append(f"score dropped {d} pts (>= {noise}) with the cleared cases at their before scores")
    return {**gate, **extra, "verdict": "FAIL" if reasons else "PASS", "reasons": reasons, "rerun": rerun}


def base_code(dev, data, dest):
    """Extract src/ at the branch's base commit into <dest>/src; returns the commit. The base is GATE_BASE,
    or the merge-base of the branch and GO_DATA's checkout. gate_run.sh calls it as --base-code <dev> <dest>."""
    git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, check=True).stdout.strip()
    base = os.environ.get("GATE_BASE") or git("-C", dev, "merge-base", "HEAD", git("-C", data, "rev-parse", "HEAD"))
    base = git("-C", dev, "rev-parse", "--verify", "--end-of-options", base + "^{commit}")   # never a git option (D-20261019-06)
    p =subprocess.Popen(["git", "-C", dev, "archive", base, "src"], stdout=subprocess.PIPE)
    with tarfile.open(fileobj=p.stdout, mode="r|") as t:
        t.extractall(dest, filter="data")
    if p.wait():
        raise RuntimeError(f"git -C {dev} archive {base} src")
    return base


def run(dev, run_dir, out):
    gp = os.path.join(out, "gate.json")
    try:
        gate = json.load(open(gp, encoding="utf-8"))
    except (OSError, ValueError):
        return                                   # gate_run.sh failed earlier; nothing to confirm
    if not eligible(gate):
        return
    here = os.path.dirname(os.path.abspath(__file__))
    data = os.environ.get("GO_DATA") or os.path.join(here, "..", "src")
    rr = os.path.join(out, "rerun")
    shutil.rmtree(rr, ignore_errors=True)
    os.makedirs(rr)
    log = open(os.path.join(rr, "rerun.log"), "a", encoding="utf-8")
    try:
        code = os.path.join(rr, "base_code")
        base = base_code(dev, data, code)
        log.write(f"base {base}\n"); log.flush()
        ids = set(gate["became_wrong"])
        cases = json.load(open(os.path.join(run_dir, "cases.json"), encoding="utf-8"))["cases"]
        sides = {"head": os.path.join(dev, "src"), "base": os.path.join(code, "src")}
        for side in sides:
            d = os.path.join(rr, side)
            os.makedirs(d)
            json.dump({"cases": [c for c in cases if c["id"] in ids]},
                      open(os.path.join(d, "cases.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
            shutil.copy(os.path.join(run_dir, "cases_state.json"), d)   # keeps the baseline answers
            shutil.copytree(os.path.join(run_dir, "manuals"), os.path.join(d, "manuals"))
        be, states = os.path.join(here, "blind_eval.py"), {"head": [], "base": []}
        rp = repeat_paths(os.path.join(out, "after", "cases_state.json"))
        if len(rp) > 1:   # a REPEATS gate: its own answers on the branch (sys_normal) and base (sys_before) are the first passes
            for f in rp:
                x = json.load(open(f, encoding="utf-8"))
                states["head"].append(x)
                states["base"].append({"grades": {c: {"sys_normal": g["sys_before"]} for c, g in x.get("grades", {}).items() if "sys_before" in g}})
            log.write(f"seeded {len(rp)} passes from the gate's repeats\n"); log.flush()
        for n in range(len(states["head"]) + 1, ROUNDS + 1):
            for side, src in sides.items():
                d = os.path.join(rr, side)
                env = {**os.environ, "GO_CODE": src, "GO_DATA": data, "SYSTEMS": "sys_normal,baseline"}
                for cmd in ([sys.executable, be, d, "reset"],
                            [sys.executable, "-X", "faulthandler", be, d, "system", "normal"],
                            [sys.executable, be, d, "grade"]):
                    log.write(f"== {side} {n}: {' '.join(cmd[1:])}\n"); log.flush()
                    if subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT).returncode:
                        raise RuntimeError(" ".join(cmd))
                keep = os.path.join(d, f"cases_state.run{n}.json")
                shutil.copy(os.path.join(d, "cases_state.json"), keep)
                states[side].append(json.load(open(keep, encoding="utf-8")))
        gate = rerate(gate, states["head"], states["base"])
    except Exception as e:                        # unconfirmed, but the single-pass FAIL still stands
        gate = {**gate, "rerun": {"error": str(e)}}
    finally:
        log.close()
        shutil.rmtree(os.path.join(rr, "base_code"), ignore_errors=True)
    json.dump(gate, open(gp, "w", encoding="utf-8"), indent=1)
    print(json.dumps(gate, indent=1))


def _selftest():
    st = lambda **s: {"grades": {"c": {"sys_normal": {"score": s["c"], "verdict": "x"}}} if "c" in s else {}}
    one = {"verdict": "FAIL", "reasons": ["became wrong: ['c']"], "became_wrong": ["c"]}
    runs = lambda *sc: [st(c=v) for v in sc]
    r = rerate(one, runs(0, 2, 3, 2, 2, 2), runs(2, 0, 3, 2, 2, 2))      # same rate (1 vs 1)
    assert r["verdict"] == "PASS" and r["reasons"] == [] and not r["rerun"]["c"]["kept"], r
    r = rerate(one, runs(0, 0, 3, 2, 2, 2), runs(2, 2, 3, 2, 2, 2))      # head worse by two
    assert r["verdict"] == "FAIL" and r["reasons"][0].startswith("became wrong: ['c']"), r
    assert "2/6 on branch vs 0/6" in r["reasons"][0], r
    r = rerate(one, runs(0, 2, 3, 2, 2, 2), runs(2, 2, 3, 2, 2, 2))      # worse by one: noise
    assert r["verdict"] == "PASS", r
    r = rerate(one, [st(c=2)] * 5 + [st()], runs(2, 2, 3, 2, 2, 2))     # a pass without a grade
    assert r["verdict"] == "FAIL" and r["rerun"]["c"]["head"][5] == "no grade", r
    assert eligible(one) and eligible({**one, "became_wrong": ["a", "b"]})
    assert not eligible({**one, "became_wrong": ["a", "b", "c"]})       # 3+ cases fail outright
    assert not eligible({**one, "reasons": ["hidden siblings below 2: ['s']", "became wrong: ['c']"]})
    assert not eligible({**one, "reasons": ["became wrong: ['c']", "score dropped 4 pts (>= 4.0)"]})  # old gate.json without drop/flip_before: fail closed
    assert not eligible({**one, "verdict": "PASS", "reasons": []})
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from retrofit_gate import gate as rgate
    def state(d):
        return {"grades": {k: {"sys_normal": {"score": v}} for k, v in d.items()}}
    ids = [f"c{i:02d}" for i in range(40)]
    sb = {i: 3 if n < 27 else 1 for n, i in enumerate(ids)}
    sa = {**sb, "c00": 0, "c01": 2, "c02": 2}
    g = rgate(state(sb), state(sa))                                     # today's shape: 78.3 -> 74.2
    assert g["drop"] == 4.1 and len(g["reasons"]) == 2 and g["became_wrong"] == ["c00"], g
    assert held_drop(g, ["c00"]) == 1.6 and eligible(g), g
    cs = lambda *sc: [{"grades": {"c00": {"sys_normal": {"score": v}}}} for v in sc]
    r = rerate(g, cs(0, 2, 3, 2, 2, 2), cs(2, 0, 3, 2, 2, 2))
    assert r["verdict"] == "PASS" and r["reasons"] == [] and r["drop_held"] == 1.6, r
    r = rerate(g, cs(0, 0, 3, 2, 2, 2), cs(2, 2, 3, 2, 2, 2))
    assert r["verdict"] == "FAIL" and r["reasons"][0].startswith("became wrong"), r
    assert r["reasons"][1].startswith("score dropped 4.1"), r
    assert eligible({**g, "drop": 6.0})                                  # held 3.5 < 4
    assert not eligible({**g, "drop": 6.5})                              # held 4.0 >= 4
    assert not eligible({**g, "reasons": ["became wrong: ['c00']",       # D-20261019-06: interval FAILs fail closed
                                          "score dropped 3.0 pts, 90% interval -5.0..-1.0 entirely below zero"]})
    r = rerate({**g, "drop": 6.5}, cs(2, 2, 3, 2, 2, 2), cs(2, 2, 3, 2, 2, 2))
    assert r["verdict"] == "FAIL" and r["drop_held"] == 4.0 and "(>= 4.0)" in r["reasons"][0], r
    w1 = lambda *sc: [{"grades": {"c": {"sys_normal": {"score": v, "verdict": "wrong" if v == 1 else "correct"}}}} for v in sc]
    r = rerate(one, w1(1, 1, 3, 3, 3, 3), w1(3, 3, 3, 3, 3, 3))
    assert r["verdict"] == "FAIL" and r["rerun"]["c"]["head_wrong"] == 2, r   # a 1-mark "wrong" counts (D-20261010-11)
    assert held_drop({"drop": 2.5, "cases": 40, "flip_before": {"c": 3}, "flip_after": {"c": 1}}, ["c"]) == 0.8
    assert settle([1, 2, 3, 2, 2, 0]) == 2 and settle([1, 1, 2, 3, 0, 2]) == 1 and settle([1, None, 2, 2, 2, 3]) == 2
    assert settle([1, None, None, 2, 2, 3]) == 1, "a missing grade fails closed"
    tmp = __import__("tempfile").mkdtemp()
    sp = os.path.join(tmp, "sib_a", "cases_state.json"); os.makedirs(os.path.dirname(sp))
    json.dump({"grades": {f"s{i}": {"sys_normal": {"score": 1}} for i in range(4)}}, open(sp, "w"))
    assert sibling_rerun(tmp, [sp], tmp, tmp, tmp)["skipped"].startswith("4 under 2"), "too many: a real miss, nothing asked"
    assert settle([1, None, None, 1, 1, 1], missing=3) == 1 and settle([3, None, None, 1, 1, 2], missing=3) == 2
    sp2 = os.path.join(tmp, "sib_b", "cases_state.json"); os.makedirs(os.path.dirname(sp2))
    json.dump({"grades": {f"s{i}": {"sys_normal": {"score": 1}, "sys_before": {"score": 1}} for i in range(4)}}, open(sp2, "w"))
    r = sibling_rerun(tmp, [sp2], tmp, tmp, tmp)
    assert r["skipped"].startswith("none") and r["asked"] == 0, "unmoved siblings are not re-asked"
    # D-20261018-02: a sibling with 3 repeat files is seeded with 3 passes per side and asked 3 more
    fake = os.path.join(tmp, "fake_be.py")
    open(fake, "w").write("import json, os, sys\nrun, ph = sys.argv[1], sys.argv[2]\np = os.path.join(run, 'cases_state.json')\n"
                          "s = json.load(open(p)) if os.path.exists(p) else {}\n"
                          "ids = [c['id'] for c in json.load(open(os.path.join(run, 'cases.json')))['cases']]\n"
                          "if ph == 'system': s['sys_normal'] = {c: {'answer': os.environ['GO_CODE']} for c in ids}\n"
                          "else: s['grades'] = {c: {'sys_normal': {'score': 3 if 'base' in os.environ['GO_CODE'] else 1}} for c in ids}\n"
                          "json.dump(s, open(p, 'w'))\n")
    d3 = os.path.join(tmp, "sib_c"); os.makedirs(d3)
    json.dump({"cases": [{"id": "s9"}]}, open(os.path.join(d3, "cases.json"), "w"))
    for k, (a, b) in enumerate([(1, 3), (3, 3), (1, 2)], 1):
        json.dump({"grades": {"s9": {"sys_normal": {"score": a}, "sys_before": {"score": b}}}},
                  open(os.path.join(d3, "cases_state.json" if k == 1 else f"cases_state.r{k}.json"), "w"))
    old_be = os.environ.get("BE"); os.environ["BE"] = fake
    try:
        r = sibling_rerun(tmp, [os.path.join(d3, "cases_state.json")], os.path.join(tmp, "dev"), tmp, os.path.join(tmp, "base"))
    finally:
        os.environ.pop("BE") if old_be is None else os.environ.update(BE=old_be)
    assert r["asked"] == 1 and r["kept"] == ["s9"] and "error" not in r, r
    assert sorted(os.listdir(os.path.join(tmp, "sib_rerun", "sib_c", "sys_normal"))) == ["r4", "r5", "r6"], "3 seeded, 3 asked"
    for f in ("cases_state.json", "cases_state.r2.json", "cases_state.r3.json"):
        g = json.load(open(os.path.join(d3, f)))["grades"]["s9"]
        assert g["sys_normal"]["passes"] == [1, 3, 1, 1, 1, 1] and g["sys_normal"]["score"] == 1, g
        assert g["sys_before"]["passes"] == [3, 3, 2, 3, 3, 3] and g["sys_before"]["score"] == 3, g
    assert json.load(open(os.path.join(d3, "cases_state.r2.json")))["grades"]["s9"]["sys_normal"]["single_pass"] == 3
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest(); sys.exit(0)
    if sys.argv[1:2] == ["--siblings"]:   # gate_run.sh, before retrofit_gate.py: <held> <dev> <base_code> <state.json...>
        here = os.path.dirname(os.path.abspath(__file__))
        res = sibling_rerun(os.path.abspath(sys.argv[2]), [os.path.abspath(a) for a in sys.argv[5:]],
                            os.path.join(os.path.abspath(sys.argv[3]), "src"), os.environ.get("GO_DATA") or os.path.join(here, "..", "src"),
                            os.path.join(os.path.abspath(sys.argv[4]), "src"))
        print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in res.items()}))   # counts only: ids stay hidden
        sys.exit(0)
    if sys.argv[1:2] == ["--base-code"]:   # gate_run.sh: the before re-asked on the base code (D-20261010-09)
        here = os.path.dirname(os.path.abspath(__file__))
        print(base_code(os.path.abspath(sys.argv[2]), os.environ.get("GO_DATA") or os.path.join(here, "..", "src"),
                        os.path.abspath(sys.argv[3])))
        sys.exit(0)
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    run(*(os.path.abspath(a) for a in sys.argv[1:]))
