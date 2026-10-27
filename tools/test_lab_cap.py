"""Self-check for the lab spending cap (D-20261010-31): tools/blind_eval.py against a stub bot, no network.

  python tools/lab_lock.py python tools/test_lab_cap.py

Each stub answer costs $0.006 (5000 output tokens at DeepSeek's list price); the cap is $0.01 a month.
a. the system phase answers two of three cases and stops with exit 3;
b. run again, it stops at once and answers nothing more;
c. a grade at the cap stops before any call (no "grades" written);
d. the cost report shows the run and the lab as stopped;
e. the CEO raises the cap from the page (tools/ceo_inbox.py "cap" row) and the run finishes.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
STUB = {
    "torch.py": "",
    "logger.py": "LOG_FILE = None\n",
    "llm.py": ("class R:\n    def json(self):\n        return {'usage': {'prompt_tokens': 0, 'completion_tokens': 5000}}\n"
               "def _post(url, *a, **k):\n    return R()\n"),
    "main.py": ("import llm\n"
                "class QueryRequest:\n    def __init__(self, **k):\n        self.__dict__.update(k)\n"
                "def _policy_setting(name, default=None):\n    return False\n"
                "def _product_names():\n    return {}\n"
                "def _traced_query(req, _user, attach=False):\n"
                "    llm._post('https://api.deepseek.com/v1/chat/completions')\n"
                "    return {'answer': 'stub ' + req.q, 'model': 'stub'}\n"),
}


def main():
    from ops_costs import build
    from ceo_inbox import apply
    with tempfile.TemporaryDirectory() as t:
        ops, code, run = os.path.join(t, "ops"), os.path.join(t, "code"), os.path.join(t, "ops", "runs", "r1")
        os.makedirs(code)
        os.makedirs(run)
        for name, text in STUB.items():
            open(os.path.join(code, name), "w").write(text)
        json.dump({"cases": [{"id": f"c{n}", "q": f"q{n}"} for n in (1, 2, 3)]}, open(os.path.join(run, "cases.json"), "w"))
        json.dump({"usd": 0.01, "per": "month"}, open(os.path.join(ops, "lab_cap.json"), "w"))
        env = {**os.environ, "LAB_OPS": ops, "GO_CODE": code, "GO_DATA": t, "GO_LAB_LOCK_HELD": "1",
               "LAB_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "not-a-key", "PYTHONIOENCODING": "utf-8"}
        state = lambda: json.load(open(os.path.join(run, "cases_state.json"), encoding="utf-8"))

        def be(*args):
            r = subprocess.run([sys.executable, os.path.join(HERE, "blind_eval.py"), run, *args], env=env,
                               capture_output=True, text=True, timeout=120)
            return r.returncode, r.stdout + r.stderr

        rc, out = be("system", "normal")
        assert rc == 3 and "lab stopped at the cap: $0.01 of $0.01 this month" in out, (rc, out)
        assert sorted(state()["sys_normal"]) == ["c1", "c2"], state()                                     # a
        assert all(r["at"] and r["usd"] == 0.006 for r in state()["sys_normal"].values()), state()
        rc, out = be("system", "normal")
        assert rc == 3 and sorted(state()["sys_normal"]) == ["c1", "c2"], (rc, out)                       # b
        rc, out = be("grade")
        assert rc == 3 and "grades" not in state(), (rc, out)                                             # c
        os.makedirs(os.path.join(t, "claude"))
        lab = build(ops, os.path.join(t, "claude"))["lab"]
        assert lab["cap"]["stopped"] and lab["cap"]["spent"] == 0.012, lab["cap"]                         # d
        assert [(r["name"], r["kind"], r["bot"]) for r in lab["rows"]] == [("r1", "run", 0.012)], lab["rows"]
        pull = os.path.join(t, "pull")
        os.makedirs(pull)
        json.dump({"status": "new", "at": "1", "id": "", "choice": "cap", "note": "1 month"}, open(os.path.join(pull, "r.json"), "w"))
        assert apply(ops, pull) == ["APPLIED r cap $1 per month"]
        assert json.load(open(os.path.join(ops, "lab_cap.json")))["usd"] == 1
        rc, out = be("system", "normal")
        assert rc == 0 and sorted(state()["sys_normal"]) == ["c1", "c2", "c3"], (rc, out)                 # e
    print("selftest ok")


if __name__ == "__main__":
    main()
