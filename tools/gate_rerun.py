"""Gate re-run: a one- or two-case "became wrong" is re-asked before it fails a branch.

  python tools/gate_rerun.py <dev_worktree> <run_dir> <out_dir>
  python tools/gate_rerun.py --selftest

tools/gate_run.sh calls it after tools/retrofit_gate.py has written <out_dir>/gate.json.
One blind pass is noisy: C20261008-22 scored 0 in 2 passes of 6 on unchanged code
(2026-10-08). So when the only reasons for FAIL are "became wrong" on 1-2 cases, alone or
with a score drop that is within --noise once those cases are held at their before scores
(D-20261010-05: one 3 -> 0 flip is 2.5 pts of 40 cases), each of
those cases is asked 6 times on the branch and 6 times on its base code, interleaved.
A case stays wrong only if the branch answers it wrong (score 0) at least 2 passes more
often than the base, or a pass has no grade (fail closed). gate.json gains "rerun" (the
per-pass grades) and a recomputed verdict; the drop is recomputed with the cleared cases
held before ruling. Siblings, a drop beyond noise or 3+ cases: untouched.
Only the open-set ids in gate.json's "became_wrong" are re-run; siblings are never read.

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

ROUNDS, MARGIN, MAX_CASES = 6, 2, 2


def held_drop(gate, cids):
    """The score drop with these became-wrong cases held at their before scores."""
    return round(gate["drop"] - 100 * sum(gate["flip_before"][c] for c in cids) / (3 * gate["cases"]), 1)


def eligible(gate):
    r, bw = gate.get("reasons", []), gate.get("became_wrong", [])
    if not (gate.get("verdict") == "FAIL" and 1 <= len(bw) <= MAX_CASES
            and all(x.startswith(("became wrong", "score dropped")) for x in r)
            and any(x.startswith("became wrong") for x in r)):
        return False
    if any(x.startswith("score dropped") for x in r):
        return ("drop" in gate and all(c in gate.get("flip_before", {}) for c in bw)
                and held_drop(gate, bw) <= gate.get("noise", 3.0))
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
        hw = sum(1 for g in h if g and g["score"] == 0)
        bw = sum(1 for g in b if g and g["score"] == 0)
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
        cleared, noise = [c for c in gate["became_wrong"] if c not in kept], gate.get("noise", 3.0)
        d = extra["drop_held"] = held_drop(gate, cleared)
        if d > noise:
            reasons.append(f"score dropped {d} pts (> {noise}) with the cleared cases at their before scores")
    return {**gate, **extra, "verdict": "FAIL" if reasons else "PASS", "reasons": reasons, "rerun": rerun}


def base_code(dev, data, dest):
    """Extract src/ at the branch's base commit into <dest>/src; returns the commit. The base is GATE_BASE,
    or the merge-base of the branch and GO_DATA's checkout. gate_run.sh calls it as --base-code <dev> <dest>."""
    git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, check=True).stdout.strip()
    base = os.environ.get("GATE_BASE") or git("-C", dev, "merge-base", "HEAD", git("-C", data, "rev-parse", "HEAD"))
    p = subprocess.Popen(["git", "-C", dev, "archive", base, "src"], stdout=subprocess.PIPE)
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
        for n in range(1, ROUNDS + 1):
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
    assert not eligible({**one, "reasons": ["became wrong: ['c']", "score dropped 4 pts (> 3)"]})  # old gate.json without drop/flip_before: fail closed
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
    assert not eligible({**g, "drop": 6.0})                              # held 3.5 > 3
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest(); sys.exit(0)
    if sys.argv[1:2] == ["--base-code"]:   # gate_run.sh: the before re-asked on the base code (D-20261010-09)
        here = os.path.dirname(os.path.abspath(__file__))
        print(base_code(os.path.abspath(sys.argv[2]), os.environ.get("GO_DATA") or os.path.join(here, "..", "src"),
                        os.path.abspath(sys.argv[3])))
        sys.exit(0)
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    run(*(os.path.abspath(a) for a in sys.argv[1:]))
