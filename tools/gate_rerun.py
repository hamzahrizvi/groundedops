"""Gate re-run: a one- or two-case "became wrong" is re-asked before it fails a branch.

  python tools/gate_rerun.py <dev_worktree> <run_dir> <out_dir>
  python tools/gate_rerun.py --selftest

tools/gate_run.sh calls it after tools/retrofit_gate.py has written <out_dir>/gate.json.
One blind pass is noisy: C20261008-22 scored 0 in 2 passes of 6 on unchanged code
(2026-10-08). So when the ONLY reason for FAIL is "became wrong" on 1-2 cases, each of
those cases is asked 6 times on the branch and 6 times on its base code, interleaved.
A case stays wrong only if the branch answers it wrong (score 0) at least 2 passes more
often than the base, or a pass has no grade (fail closed). gate.json gains "rerun" (the
per-pass grades) and a recomputed verdict. Siblings, a score drop or 3+ cases: untouched.
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


def eligible(gate):
    r = gate.get("reasons", [])
    return (gate.get("verdict") == "FAIL" and len(r) == 1 and r[0].startswith("became wrong")
            and 1 <= len(gate.get("became_wrong", [])) <= MAX_CASES)


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
    return {**gate, "verdict": "FAIL" if reasons else "PASS", "reasons": reasons, "rerun": rerun}


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
    git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, check=True).stdout.strip()
    try:
        base = os.environ.get("GATE_BASE") or git("-C", dev, "merge-base", "HEAD", git("-C", data, "rev-parse", "HEAD"))
        log.write(f"base {base}\n"); log.flush()
        code = os.path.join(rr, "base_code")
        p = subprocess.Popen(["git", "-C", dev, "archive", base, "src"], stdout=subprocess.PIPE)
        with tarfile.open(fileobj=p.stdout, mode="r|") as t:
            t.extractall(code, filter="data")
        if p.wait():
            raise RuntimeError(f"git -C {dev} archive {base} src")
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
    assert not eligible({**one, "reasons": ["became wrong: ['c']", "score dropped 4 pts (> 3)"]})
    assert not eligible({**one, "verdict": "PASS", "reasons": []})
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest(); sys.exit(0)
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    run(*(os.path.abspath(a) for a in sys.argv[1:]))
