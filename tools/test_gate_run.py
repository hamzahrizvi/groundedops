"""Self-check for tools/gate_run.sh with a fake blind_eval.py: no bot, no grader, no network.

  python tools/test_gate_run.py      (from Git Bash; GATE_BASH=<bash.exe> to pick another bash)

1. a branch whose own commits touch nothing under src/: office_only PASS, blind_eval never called (D-20261010-07);
2. a branch touching src/, run answered on today's bot model: full gate, the stored before is used;
3. the same branch, run answered on another bot model: the before is re-asked on the base code (D-20261010-09).
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
open(os.environ["FAKE_LOG"], "a").write(phase + " " + os.path.basename(run) + "\\n")
p = os.path.join(run, "cases_state.json")
if phase == "models":
    same = os.environ["FAKE_SAME"] == "1"
    print(json.dumps({"run": ["deepseek-v4-flash" if same else "gpt-5-mini"], "lab": "deepseek-v4-flash", "same": same}))
elif phase in ("system", "grade"):
    s = json.load(open(p))
    if phase == "system":
        s["sys_normal"] = {"c1": {"answer": open(os.path.join(os.environ["GO_CODE"], "a.py")).read()}}
    else:
        s["grades"] = {"c1": {k: {"score": 3, "verdict": "correct"} for k in os.environ.get("SYSTEMS", "sys_normal").split(",")}}
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
    put(os.path.join(tmp, "fake_be.py"), FAKE)

    def gate(name, same):
        out, calls = os.path.join(tmp, name), os.path.join(tmp, name + ".calls")
        e = {**env, "PY": fwd(sys.executable), "BE": fwd(os.path.join(tmp, "fake_be.py")),
             "GO_DATA": fwd(os.path.join(base, "src")), "FAKE_LOG": calls, "FAKE_SAME": same}
        r = subprocess.run([os.environ.get("GATE_BASH") or shutil.which("bash"), fwd(os.path.join(HERE, "gate_run.sh")),
                            fwd(dev), fwd(run), fwd(out)], env=e, capture_output=True, text=True)
        assert not os.path.exists(os.path.join(out, "FAILED")), (r.stdout, r.stderr)
        seen = open(calls).read().splitlines() if os.path.exists(calls) else []
        return json.load(open(os.path.join(out, "gate.json"))), seen, out

    g, seen, _ = gate("g1", "1")
    assert g == {"verdict": "PASS", "reasons": ["no product code changed"], "fixed": [], "became_wrong": [],
                 "cases": 0, "siblings": 0, "office_only": True}, g
    assert seen == [], seen                                                   # no bot, no grader
    put(os.path.join(dev, "src", "a.py"), "x = 2\n"); git(dev, "commit", "-qam", "product change")
    g, seen, out = gate("g2", "1")
    assert "office_only" not in g and g["verdict"] == "PASS" and g["before"] == "stored", g
    assert seen == ["remark run", "models run", "reset after", "system after", "grade after"], seen
    assert json.load(open(os.path.join(out, "after", "cases_state.json")))["sys_before"]["c1"]["answer"] == "stored"
    g, seen, out = gate("g3", "0")
    assert g["before"] == "re-asked" and g["models"]["run"] == ["gpt-5-mini"], g
    assert seen == ["remark run", "models run", "reset before", "system before",
                    "reset after", "system after", "grade after"], seen
    after = json.load(open(os.path.join(out, "after", "cases_state.json")))
    assert (after["sys_before"]["c1"]["answer"], after["sys_normal"]["c1"]["answer"]) == ("x = 1\n", "x = 2\n")  # before on base code, after on the branch, one marking pass
    assert not os.path.exists(os.path.join(out, "base_code"))
    git(base, "worktree", "remove", "--force", dev)
    shutil.rmtree(tmp, ignore_errors=True)
    print("selftest ok")


if __name__ == "__main__":
    main()
