"""Self-check for tools/gate_run.sh with a fake blind_eval.py: no bot, no grader, no network.

  python tools/test_gate_run.py      (from Git Bash; GATE_BASH=<bash.exe> to pick another bash)

1. a branch whose own commits touch nothing under src/: office_only PASS, blind_eval never called (D-20261010-07);
2. a branch touching src/, run answered on today's bot model: full gate, the stored before is used;
3. the same branch, run answered on another bot model: the before is re-asked on the base code (D-20261010-09);
4. the after pass is marked with the run's manuals/ (D-20261010-23);
5. blind_eval.py stopped at the lab cap (exit 3): FAILED says so and no gate.json is written (D-20261010-31);
6. an unmoved hidden sibling (under 2 on base and branch) passes and a raised one is counted (D-20261016-10);
7. a sibling 2+ on base and under 2 on the branch is settled 6 times on each side and fails the gate.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = '''import json, os, sys
run, phase = sys.argv[1], sys.argv[2]
tag = " manuals" if phase == "grade" and os.path.isdir(os.path.join(run, "manuals")) else ""
open(os.environ["FAKE_LOG"], "a").write(phase + " " + os.path.basename(run) + tag + "\\n")
p = os.path.join(run, "cases_state.json")
if os.environ.get("FAKE_CAP") and phase != "models":
    sys.exit(3)
if phase == "models":
    same = os.environ["FAKE_SAME"] == "1"
    print(json.dumps({"run": ["deepseek-v4-flash" if same else "gpt-5-mini"], "lab": "deepseek-v4-flash", "same": same}))
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
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
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

    def gate(name, same, cap="", sibs=()):
        out, calls = os.path.join(tmp, name), os.path.join(tmp, name + ".calls")
        e = {**env, "PY": fwd(sys.executable), "BE": fwd(os.path.join(tmp, "fake_be.py")),
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
    git(base, "worktree", "remove", "--force", dev)
    shutil.rmtree(tmp, ignore_errors=True)
    print("selftest ok")


if __name__ == "__main__":
    main()
