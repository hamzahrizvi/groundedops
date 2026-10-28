"""Self-check for blind_eval.py --no-product and the wrongproduct count (D-20261022-10), fake bot and grader, no network.

  python tools/test_no_product.py
"""
import contextlib
import io
import json
import os
import re
import runpy
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
BE = os.path.join(HERE, "blind_eval.py")
PROMPTS = []

STUB = {
    "torch.py": "",
    "logger.py": "LOG_FILE = None\n",
    "llm.py": "def _post(url, *a, **k): raise RuntimeError('no network')\n",
    "main.py": (
        "class QueryRequest:\n"
        "    def __init__(self, **k): self.__dict__.update(k)\n"
        "def _policy_setting(name, default): return False\n"
        "def _product_names(): return {}\n"
        "CALLS = []\n"
        "def _traced_query(req, _x, attach=True):\n"
        "    CALLS.append(req.product)\n"
        "    doc = 'other.pdf' if req.product is None and req.q.startswith('SECRET-a') else 'm.pdf'\n"
        "    return {'answer': 'from ' + doc, 'sources': [{'source': doc, 'pages': [1]}]}\n"),
}


def fake_post(url, json=None, **_k):
    text = json["messages"][-1]["content"]
    PROMPTS.append(text)
    labels = re.findall(r"ANSWER ([A-G]):\n(.*?)(?=\n\nANSWER [A-G]:|\n\nReply with JSON)", text, re.S)
    content = __import__("json").dumps({**{k: {"score": 0 if "other.pdf" in a else 3, "verdict": "x"} for k, a in labels}, "best": "tie", "why": "x"})
    return types.SimpleNamespace(raise_for_status=lambda: None,
                                 json=lambda: {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 10}})


def be(run, *argv):
    os.environ.update(REPEAT="1", SYSTEMS="sys_normal")
    sys.argv = [BE, run, *argv]
    out, code = io.StringIO(), 0
    try:
        with contextlib.redirect_stdout(out):
            runpy.run_path(BE, run_name="__main__")
    except SystemExit as e:
        code = e.code or 0
    return code, out.getvalue()


def state(run):
    return json.load(open(os.path.join(run, "cases_state.json"), encoding="utf-8"))


def main():
    import requests
    requests.post = fake_post
    tmp = tempfile.mkdtemp()
    try:
        src = os.path.join(tmp, "src"); os.makedirs(src)
        for f, body in STUB.items():
            open(os.path.join(src, f), "w").write(body)
        os.environ.update(GO_CODE=src, GO_DATA=src, LAB_OPS=os.path.join(tmp, "ops"), GO_LAB_LOCK_HELD="1",
                          DEEPSEEK_API_KEY="test-not-a-key", LAB_PROVIDER="deepseek")
        cases = [{"id": "a", "q": "SECRET-a q", "turn1": "SECRET-a t1", "type": "fact", "expected": "answered", "reference": "r", "product": "p"},
                 {"id": "b", "q": "SECRET-b q", "type": "fact", "expected": "answered", "reference": "r", "product": "p"}]

        def fresh(name):
            run = os.path.join(tmp, name); os.makedirs(os.path.join(run, "manuals"))
            open(os.path.join(run, "manuals", "p.txt"), "w").write("[m.pdf p1] text")
            open(os.path.join(run, "manuals", "q.txt"), "w").write("[other.pdf p1] text")
            json.dump({"cases": cases}, open(os.path.join(run, "cases.json"), "w"))
            return run

        main_mod = lambda: sys.modules["main"]
        typed, picker = fresh("typed"), fresh("picker")
        assert be(typed, "system", "normal", "--no-product")[0] == 0
        assert main_mod().CALLS == [None, None, None], main_mod().CALLS                                   # 1
        assert state(typed)["no_product"] is True
        main_mod().CALLS.clear()
        assert be(picker, "system", "normal")[0] == 0 and main_mod().CALLS == ["p", "p", "p"]

        assert be(typed, "grade")[0] == 0 and be(picker, "grade")[0] == 0                                   # 2
        assert all("PRODUCT SELECTED IN THE CHAT: none (unscoped chat)" in t for t in PROMPTS[:2]), PROMPTS[0]
        assert all("PRODUCT SELECTED IN THE CHAT: p\n" in t for t in PROMPTS[2:]) and len(PROMPTS) == 4

        assert be(typed, "system", "normal")[0] != 0                                                        # 3
        assert be(picker, "system", "normal", "--no-product")[0] != 0
        assert be(typed, "reset")[0] == 0 and "no_product" not in state(typed)

        be(typed, "system", "normal", "--no-product"); be(typed, "grade")
        code, out = be(typed, "wrongproduct", picker)                                                       # 4
        assert code == 0 and "SECRET" not in out, out
        row = [l for l in out.splitlines() if l.startswith("fact |")][0]
        assert row == "fact | 1 / 1 / 2 | 0 / 0 / 2", row
        print("ok")
    finally:
        os.chdir(HERE)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
