"""Self-check for N answers per question (D-20261018-02): tools/repeat_spread.py on a synthetic 3-repeat run,
and tools/blind_eval.py --repeats against a stub bot (no network).

  python tools/lab_lock.py python tools/test_repeat_spread.py

a. spread: readings, spreads, per-question SD and Miller's MDE for K=1 and K=3; ADOPT on a 4-mark type spread;
   an arm copied from repeat 1 is skipped;
b. blind_eval.py system --repeats 2 writes cases_state.json and cases_state.r2.json, each its own answers;
c. a grade of repeat 2 copies the baseline it lacks from repeat 1 (copied_from_r1), even when the cap then stops it;
d. reset --repeats 2 drops both repeats' answers.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from repeat_spread import spread, table  # noqa: E402
from test_lab_cap import STUB  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as t:
        run = os.path.join(t, "run")
        os.makedirs(run)
        N = 40   # 4 lookup questions that move, 36 vague ones that do not
        cases = [{"id": f"q{i}", "q": f"q{i}", "type": "lookup" if i < 4 else "vague"} for i in range(N)]
        json.dump({"cases": cases}, open(os.path.join(run, "cases.json"), "w"))
        lookup = [[3, 3, 3, 3], [3, 1, 3, 3], [3, 3, 0, 3]]        # per repeat, q0..q3: 12, 10, 9 marks
        vague = [[2] * 36] * 3
        for k in range(3):
            g = {f"q{i}": {"sys_normal": {"score": (lookup[k] + vague[k])[i]}, "baseline": {"score": 3}} for i in range(N)}
            json.dump({"grades": g, **({"copied_from_r1": ["baseline"]} if k else {})},
                      open(os.path.join(run, "cases_state.json" if k == 0 else f"cases_state.r{k + 1}.json"), "w"))
        r = spread(run)
        a = r["arms"]["sys_normal"]
        assert r["repeats"] == 3 and a["n"] == N, r
        assert a["overall"] == [70.0, 68.3, 67.5] and a["overall_spread"] == 2.5, a
        assert a["by_type"]["lookup"] == {"marks": [12, 10, 9], "of": 12, "spread": 3}, a
        assert a["by_type"]["vague"]["spread"] == 0 and a["sd_nonzero"] == 2, a
        # var: q1 [3,1,3] = 4/3, q2 [3,3,0] = 3, the rest 0 -> mean 13/120; MDE_1 = 2.9266 * sqrt(2 * 13/120 / 40) * 100/3
        assert a["var_mean"] == round(13 / 120, 4) and a["mde_pts"] == {"K1": 7.2, "K3": 4.1}, a
        assert r["adopt"] is False and "2.5 pts" in r["why"], r                     # 2.5 pts, 3 marks: under both bars
        assert "skipped" in r["arms"]["baseline"], r                                # a copied arm is one answer
        assert "| lookup (of 12) | 12 | 10 | 9 | 3 |" in table(r), table(r)
        for k in (1, 2):                                                            # 4 marks in one type alone adopts
            g = json.load(open(os.path.join(run, f"cases_state.r{k + 1}.json")))
            for i in range(N):
                g["grades"][f"q{i}"]["sys_normal"]["score"] = 3 if i < 4 else 2
            json.dump(g, open(os.path.join(run, f"cases_state.r{k + 1}.json"), "w"))
        r = spread(run)
        assert r["arms"]["sys_normal"]["overall_spread"] == 0.0 and r["adopt"] is False, r
        g["grades"]["q0"]["sys_normal"]["score"] = 0; g["grades"]["q1"]["sys_normal"]["score"] = 2
        json.dump(g, open(os.path.join(run, "cases_state.r3.json"), "w"))
        r = spread(run)
        assert r["arms"]["sys_normal"]["overall_spread"] == 3.3 and r["adopt"] is True, r       # 4 marks alone: 3.3 pts overall

        # b-d: blind_eval.py --repeats against the stub bot of test_lab_cap.py
        ops, code, br = os.path.join(t, "ops"), os.path.join(t, "code"), os.path.join(t, "ops", "runs", "rep")
        os.makedirs(code); os.makedirs(br)
        for name, text in STUB.items():
            open(os.path.join(code, name), "w").write(text)
        json.dump({"cases": [{"id": "c1", "q": "q1"}, {"id": "c2", "q": "q2"}]}, open(os.path.join(br, "cases.json"), "w"))
        env = {**os.environ, "LAB_OPS": ops, "GO_CODE": code, "GO_DATA": t, "GO_LAB_LOCK_HELD": "1",
               "LAB_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "not-a-key", "PYTHONIOENCODING": "utf-8"}
        env.pop("REPEAT", None)
        be = lambda *a: subprocess.run([sys.executable, os.path.join(HERE, "blind_eval.py"), br, *a], env=env,
                                       capture_output=True, text=True, timeout=120)
        st = lambda f: json.load(open(os.path.join(br, f), encoding="utf-8"))
        p = be("system", "normal", "--repeats", "2")
        assert p.returncode == 0, p.stdout + p.stderr
        assert sorted(st("cases_state.json")["sys_normal"]) == sorted(st("cases_state.r2.json")["sys_normal"]) == ["c1", "c2"]   # b
        r1 = st("cases_state.json")
        r1["baseline"] = {c: {"answer": "whole manual", "wall": 1, "usd": 0} for c in ("c1", "c2")}
        json.dump(r1, open(os.path.join(br, "cases_state.json"), "w"))
        json.dump({"usd": 0, "per": "month"}, open(os.path.join(ops, "lab_cap.json"), "w"))   # stopped: no grader call
        p = be("grade", "--repeats", "2")
        assert p.returncode == 3 and "lab stopped at the cap" in p.stdout, p.stdout + p.stderr  # r1 stops first: exit carried
        assert "copied_from_r1" not in st("cases_state.json")
        p = subprocess.run([sys.executable, os.path.join(HERE, "blind_eval.py"), br, "grade"], env={**env, "REPEAT": "2"},
                           capture_output=True, text=True, timeout=120)
        r2 = st("cases_state.r2.json")
        assert p.returncode == 3 and r2["copied_from_r1"] == ["baseline"] and r2["baseline"] == r1["baseline"], r2   # c
        assert "grades" not in r2
        p = be("reset", "--repeats", "2")
        assert p.returncode == 0 and "sys_normal" not in st("cases_state.json") and "sys_normal" not in st("cases_state.r2.json"), p.stdout
        assert st("cases_state.r2.json")["copied_from_r1"] == ["baseline"] and "baseline" in st("cases_state.r2.json")   # d
    print("selftest ok")


if __name__ == "__main__":
    main()
