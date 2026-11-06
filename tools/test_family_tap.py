"""Self-check for blind_eval.py tapping the family card (D-20261106-02), fake bot and grader, no network.

  python tools/test_family_tap.py
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
        "    CALLS.append((req.q, req.product))\n"
        "    if req.q == 'q (NV9 USB)': return {'answer': 'real answer'}\n"
        "    if req.q.startswith('fam'): return {'role': 'clarify', 'model': 'none', 'answer': 'Which NV9?', 'family_options': [{'label': 'NV9 USB', 'product': 'nv9usb', 'send': 'q (NV9 USB)'}, {'label': 'Not sure', 'product': '', 'send': 'x'}]}\n"
        "    return {'answer': 'plain', 'family_options': []}\n"),
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
        cases = [{"id": "a", "q": "fam q", "type": "fact", "expected": "answered", "reference": "r", "product": "nv9usb"},
                 {"id": "b", "q": "fam q2", "type": "fact", "expected": "answered", "reference": "r"},
                 {"id": "d", "q": "fam q3", "type": "vague", "expected": "clarify", "reference": "r", "product": "nv9usb"},
                 {"id": "c", "q": "plain q", "type": "fact", "expected": "answered", "reference": "r", "product": "nv9usb"}]
        run = os.path.join(tmp, "r"); os.makedirs(os.path.join(run, "manuals"))
        open(os.path.join(run, "manuals", "nv9usb.txt"), "w").write("[m.pdf p1] text")
        json.dump({"cases": cases}, open(os.path.join(run, "cases.json"), "w"))
        assert be(run, "system", "normal")[0] == 0
        calls = sys.modules["main"].CALLS
        assert calls == [("fam q", "nv9usb"), ("q (NV9 USB)", "nv9usb"), ("fam q2", None), ("fam q3", "nv9usb"), ("plain q", "nv9usb")], calls
        rows = state(run)["sys_normal"]
        assert rows["a"]["answer"] == "real answer" and rows["a"]["family_ask"]["answer"] == "Which NV9?"
        assert rows["b"]["answer"] == "Which NV9?" and "family_ask" not in rows["b"]
        assert rows["d"]["answer"] == "Which NV9?" and "family_ask" not in rows["d"]
        assert rows["c"]["answer"] == "plain" and "family_ask" not in rows["c"]
        assert be(run, "grade")[0] == 0
        assert state(run)["summary"]["sys_normal"]["family_asked"] == 1
        # typed first, no product picked: the tap sends no product either, as the widget does
        run2 = os.path.join(tmp, "r2"); shutil.copytree(run, run2); os.remove(os.path.join(run2, "cases_state.json"))
        del calls[:]
        assert be(run2, "system", "normal", "--no-product")[0] == 0
        assert calls[:2] == [("fam q", None), ("q (NV9 USB)", None)], calls
        assert state(run2)["sys_normal"]["a"]["answer"] == "real answer"
        print("ok")
    finally:
        os.chdir(HERE)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
