"""Self-check for tools/gate_run.sh with a fake blind_eval.py: no bot, no grader, no network.

  python tools/test_gate_run.py      (from Git Bash; GATE_BASH=<bash.exe> to pick another bash)

1. a branch whose own commits touch nothing under src/: office_only PASS, blind_eval never called (D-20261010-07);
2. a branch touching src/, run answered on today's bot model: full gate, the stored before is used;
3. the same branch, run answered on another bot model: the before is re-asked on the base code (D-20261010-09);
4. the after pass is marked with the run's manuals/ (D-20261010-23);
5. blind_eval.py stopped at the lab cap (exit 3): FAILED says so and no gate.json is written (D-20261010-31);
6. an unmoved hidden sibling (under 2 on base and branch) passes and a raised one is counted (D-20261016-10);
7. a sibling 2+ on base and under 2 on the branch is settled 6 times on each side and fails the gate;
8. REPEATS=2: the before is re-asked twice on the base code, each repeat's before rides into its after, gate.json
   carries mean_of_repeats; GATE_RULE=mean makes it the verdict (D-20261018-02);
9. GATE_BASE is checked (D-20261019-06): the unmerged branch tip or an option-looking value runs the full gate
   (and writes no file); a commit both the branch and the product checkout contain is used and shows as gate_base;
10. with REPEATS and GATE_RULE unset the gate asks 3 answers per question and rules on their mean (adopted 2026-10-19);
11. a merged branch re-gated with GATE_BASE at its own tip: office_only PASS with gate_base.
12. REPEATS=2 with a sibling: both ADAPTIVE_FROM values (the open set's and the sibling's) name a file Windows python
    can open, as the fake lab checks (D-20261021-21).
Every full gate carries cost_usd and wall_s. Cases 1-7, 9 and 11 pin REPEATS=1, GATE_RULE=r1: the one-answer path.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = '''import json, os, subprocess, sys
if "--repeats" in sys.argv:   # as blind_eval.py: each repeat its own process with REPEAT=k
    i = sys.argv.index("--repeats"); n = int(sys.argv[i + 1]); a = sys.argv[1:i] + sys.argv[i + 2:]
    for k in range(1, n + 1):
        rc = subprocess.call([sys.executable, __file__] + a, env={**os.environ, "REPEAT": str(k)})
        if rc: sys.exit(rc)
    sys.exit(0)
k = int(os.environ.get("REPEAT", "1"))
run, phase = sys.argv[1], sys.argv[2]
if phase == "system" and os.environ.get("ADAPTIVE_FROM") and not os.path.exists(os.environ["ADAPTIVE_FROM"].rsplit(":", 1)[0]):
    sys.exit(9)   # as blind_eval.py: Windows python must open ADAPTIVE_FROM's file (D-20261021-21)
tag = " manuals" if phase == "grade" and os.path.isdir(os.path.join(run, "manuals")) else ""
open(os.environ["FAKE_LOG"], "a").write(phase + " " + os.path.basename(run) + tag + (" #%d" % k if k > 1 else "") + "\\n")
p = os.path.join(run, "cases_state.json" if k == 1 else "cases_state.r%d.json" % k)
if os.environ.get("FAKE_CAP") and phase != "models":
    sys.exit(3)
if phase == "models":
    same = os.environ["FAKE_SAME"] == "1"
    print(json.dumps({"run": ["deepseek-v4-flash" if same else "gpt-5-mini"], "lab": "deepseek-v4-flash", "same": same}))
elif phase == "reset":
    s = json.load(open(p)) if os.path.exists(p) else {}
    json.dump({x: v for x, v in s.items() if not x.startswith("sys_") and x != "grades"}, open(p, "w"))
elif phase in ("system", "grade"):
    s = json.load(open(p)) if os.path.exists(p) else {}   # a sibling folder starts without a state
    if phase == "system":
        code = open(os.path.join(os.environ["GO_CODE"], "a.py")).read()
        s["sys_normal"] = {c["id"]: {"answer": code} for c in json.load(open(os.path.join(run, "cases.json")))["cases"]}
    else:
        def mark(cid, ans):   # sibling "s<base><branch>": first digit on the base code (x = 1), second on the branch
            return int(cid[1 if "x = 1" in ans else 2]) if cid[0] == "s" else 3
        s["grades"] = {cid: {k: {"score": mark(cid, s.get(k, {}).get(cid, {}).get("answer", "")), "verdict": "correct"}
                             for k in os.environ.get("SYSTEMS", "sys_normal").split(",")} for cid in s["sys_normal"]}
    json.dump(s, open(p, "w"))
'''


def main():
    tmp = tempfile.mkdtemp()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "REPEATS": "1", "GATE_RULE": "r1", "LAB_PAUSE": "0"}
    git = lambda cwd, *a: subprocess.run(["git", "-C", cwd, *a], env=env, check=True, capture_output=True)
    fwd = lambda p: p.replace("\\", "/")

    def put(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write(text)

    base, dev, run = (os.path.join(tmp, n) for n in ("base", "dev", "run"))
    put(os.path.join(base, "src", "a.py"), "x = 1\n")
    put(os.path.join(base, "tools", "t.txt"), "1\n")
    git(base, "init", "-q"); git(base, "add", "-A"); git(base, "commit", "-qm", "base")
    git(base, "worktree", "add", "-q", "-b", "dev", dev)
    put(os.path.join(dev, "tools", "t.txt"), "2\n"); git(dev, "commit", "-qam", "office only")
    put(os.path.join(run, "cases.json"), json.dumps({"cases": [{"id": "c1"}]}))
    put(os.path.join(run, "cases_state.json"), json.dumps({"sys_normal": {"c1": {"answer": "stored"}},
                                                           "grades": {"c1": {"sys_normal": {"score": 3}}}}))
    put(os.path.join(run, "manuals", "m.txt"), "[m.pdf p1] text\n")
    put(os.path.join(tmp, "fake_be.py"), FAKE)

    def gate(name, same, cap="", sibs=(), extra=None):
        out, calls = os.path.join(tmp, name), os.path.join(tmp, name + ".calls")
        e = {k: v for k, v in {**env, **(extra or {})}.items() if v is not None}   # None: unset
        e = {**e, "PY": fwd(sys.executable), "BE": fwd(os.path.join(tmp, "fake_be.py")),
             "GO_DATA": fwd(os.path.join(base, "src")), "FAKE_LOG": calls, "FAKE_SAME": same, "FAKE_CAP": cap,
             "GATE_HELD": fwd(os.path.join(tmp, name + ".held"))}
        r = subprocess.run([os.environ.get("GATE_BASH") or shutil.which("bash"), fwd(os.path.join(HERE, "gate_run.sh")),
                            fwd(dev), fwd(run), fwd(out), *[fwd(p) for p in sibs]], env=e, capture_output=True, text=True)
        if cap:
            return open(os.path.join(out, "FAILED")).read().strip(), os.path.exists(os.path.join(out, "gate.json"))
        assert not os.path.exists(os.path.join(out, "FAILED")), (r.stdout, r.stderr, open(os.path.join(out, "FAILED")).read(), open(os.path.join(out, "gate.log")).read()[-1500:])
        seen = open(calls).read().splitlines() if os.path.exists(calls) else []
        return json.load(open(os.path.join(out, "gate.json"))), seen, out

    g, seen, _ = gate("g1", "1")
    assert g == {"verdict": "PASS", "reasons": ["no product code changed"], "fixed": [], "became_wrong": [],
                 "cases": 0, "siblings": 0, "office_only": True}, g
    assert seen == [], seen                                                   # no bot, no grader
    put(os.path.join(dev, "src", "a.py"), "x = 2\n"); git(dev, "commit", "-qam", "product change")
    g, seen, out = gate("g2", "1")
    assert "office_only" not in g and g["verdict"] == "PASS" and g["before"] == "stored", g
    assert seen == ["remark run", "models run", "reset after", "system after", "grade after manuals"], seen
    assert g["manuals"] == ["m.txt"], g
    assert json.load(open(os.path.join(out, "after", "cases_state.json")))["sys_before"]["c1"]["answer"] == "stored"
    assert g["cost_usd"] == 0.0 and isinstance(g["wall_s"], int) and "gate_base" not in g, g   # the fake lab logs no usd
    g, seen, out = gate("g3", "0")
    assert g["before"] == "re-asked" and g["models"]["run"] == ["gpt-5-mini"], g
    assert seen == ["remark run", "models run", "reset before", "system before",
                    "reset after", "system after", "grade after manuals"], seen
    after = json.load(open(os.path.join(out, "after", "cases_state.json")))
    assert (after["sys_before"]["c1"]["answer"], after["sys_normal"]["c1"]["answer"]) == ("x = 1\n", "x = 2\n")  # before on base code, after on the branch, one marking pass
    assert not os.path.exists(os.path.join(out, "base_code"))
    sa, sb = os.path.join(tmp, "hid", "sibA.json"), os.path.join(tmp, "hid", "sibB.json")
    put(sa, json.dumps({"cases": [{"id": "s11", "q": "Hidden question one, unmoved by the fix?"},
                                  {"id": "s13", "q": "Hidden question two, raised by the fix?"},
                                  {"id": "s33", "q": "Hidden question three, right on both?"}]}))
    put(sb, json.dumps({"cases": [{"id": "s31", "q": "Hidden question four, dropped by the fix?"}]}))
    g, seen, out = gate("g5", "1", sibs=[sa])          # same bot model: base code extracted for the siblings alone
    assert g["verdict"] == "PASS" and g["siblings_unmoved"] == ["s11"] and g["siblings_fixed"] == ["s13"], g
    assert g["sibling_marks"] == {"s11": {"before": 1, "after": 1}, "s13": {"before": 1, "after": 3},
                                  "s33": {"before": 3, "after": 3}}, g
    assert seen == ["remark run", "models run", "reset after", "system after", "grade after manuals",
                    "system base", "system sib_sibA", "grade sib_sibA"], seen      # unmoved: not re-asked
    assert not os.path.exists(os.path.join(out, "base_code"))
    g, seen, out = gate("g6", "1", sibs=[sb])
    assert g["verdict"] == "FAIL" and g["reasons"] == ["hidden siblings below 2 on the branch, 2+ on base: ['s31']"], g
    assert g["sibling_marks"] == {"s31": {"before": 3, "after": 1}}, g
    assert seen.count("system r2") == 2 and seen.count("system r6") == 2, seen   # settled on both sides
    held = json.load(open(os.path.join(tmp, "g6.held", "sib_sibB", "cases_state.json")))["grades"]["s31"]
    assert held["sys_normal"]["passes"] == [1] * 6 and held["sys_before"]["passes"] == [3] * 6, held
    assert gate("g4", "1", cap="1") == ("lab stopped at the cap", False)   # fails closed: no PASS without a measurement
    g, seen, out = gate("g7", "1", extra={"REPEATS": "2"})
    assert seen == ["remark run", "models run", "reset before", "reset before #2", "system before",   # repeat 1 graded
                    "reset after", "reset after #2", "system after", "grade after manuals",            # first (ADAPTIVE),
                    "system before #2", "system after #2", "grade after manuals #2"], seen             # then the rest
    assert g["verdict"] == "PASS" and g["mean_of_repeats"]["repeats"] == 2 and g["repeats"] == 1 and g["before"] == "re-asked", g
    r2 = json.load(open(os.path.join(out, "after", "cases_state.r2.json")))
    assert (r2["sys_before"]["c1"]["answer"], r2["sys_normal"]["c1"]["answer"]) == ("x = 1\n", "x = 2\n"), r2
    g, _, _ = gate("g8", "1", extra={"REPEATS": "2", "GATE_RULE": "mean"})
    assert g["repeats"] == 2 and g["r1"]["repeats"] == 1 and "mean_of_repeats" not in g, g
    rev = lambda c, r: subprocess.run(["git", "-C", c, "rev-parse", r], capture_output=True, text=True).stdout.strip()
    tip, b0 = rev(dev, "HEAD"), rev(base, "HEAD")
    g, seen, out = gate("g9", "1", extra={"GATE_BASE": tip})          # unmerged tip: not in the product checkout
    assert "office_only" not in g and "gate_base" not in g and "system after" in seen, (g, seen)
    assert "is not a commit both the branch and the product checkout contain" in open(os.path.join(out, "gate.log")).read()
    g, seen, _ = gate("g9b", "1", extra={"GATE_BASE": "--output=x"})  # never reaches git as an option
    assert "office_only" not in g and "gate_base" not in g and "system after" in seen, (g, seen)
    assert not any(os.path.exists(os.path.join(d, "x")) for d in (dev, tmp, base, os.getcwd())), "GATE_BASE wrote a file"
    g, seen, _ = gate("g9c", "1", extra={"GATE_BASE": b0[:10]})        # the base commit: used, the src change is after it
    assert "office_only" not in g and g["gate_base"] == b0 and "system after" in seen, (g, seen)
    g, seen, _ = gate("g10", "1", extra={"REPEATS": None, "GATE_RULE": None})
    assert g["repeats"] == 3 and g["r1"]["repeats"] == 1 and seen.count("system after #3") == 1, (g, seen)
    g, seen, _ = gate("g12", "1", sibs=[sa], extra={"REPEATS": "2"})   # ADAPTIVE_FROM for the open set and a sibling
    assert seen.count("system before #2") == 1 and seen.count("system base #2") == 1 and seen.count("system sib_sibA #2") == 1, seen
    git(base, "merge", "-q", "--ff-only", "dev")                       # merged: re-gated from its own tip
    g, seen, _ = gate("g11", "1", extra={"GATE_BASE": tip})
    assert g.get("office_only") and g["gate_base"] == tip and seen == [], (g, seen)
    git(base, "worktree", "remove", "--force", dev)
    shutil.rmtree(tmp, ignore_errors=True)
    print("selftest ok")


if __name__ == "__main__":
    main()
