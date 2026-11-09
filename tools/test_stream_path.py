"""Self-check for blind_eval.py --path stream (D-20261109-08), stub app and grader, no network.

  python tools/test_stream_path.py     (needs fastapi: run it with the repo .venv)
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

STUB = {
    "torch.py": "",
    "logger.py": "LOG_FILE = None\n",
    "llm.py": "def _post(url, *a, **k): raise RuntimeError('no network')\n",
    "quota.py": "def issue_token(uid, tier='member', ttl=0): return 'tok-' + uid\n",
    "main.py": (
        "import json\n"
        "from fastapi import FastAPI, Request\n"
        "from fastapi.responses import StreamingResponse\n"
        "class QueryRequest:\n"
        "    def __init__(self, **k): self.__dict__.update(k)\n"
        "def _policy_setting(name, default): return False\n"
        "def _product_names(): return {'nv9usb': 'NV9 USB', 'bv30': 'BV30'}\n"
        "def _traced_query(*a, **k): raise AssertionError('query path used on the stream path')\n"
        "CALLS = []\n"
        "app = FastAPI()\n"
        "def ev(name, d): return 'event: ' + name + '\\ndata: ' + json.dumps(d) + '\\n\\n'\n"
        "@app.post('/widget/ask/stream')\n"
        "async def stream(request: Request):\n"
        "    body = await request.json()\n"
        "    q = body['q']\n"
        "    CALLS.append((q, body.get('product'), request.headers.get('authorization')))\n"
        "    if q == 'cmp q':\n"
        "        t = ev('status', {'id': 'step'}) * 2 + ev('part', {'title': 'NV9 USB', 'answer': 'NV9 draws 1.5 A at 12 V.'})\n"
        "        t += ev('part', {'title': 'BV30', 'answer': 'BV30 draws 2 A.'}) + ev('meta', {'role': 'comparison', 'sources': []})\n"
        "        t += ev('delta', {'text': '### Comparison\\n\\n'}) + ev('delta', {'text': 'NV9 USB 1.5 A vs BV30 2 A; BV30 weighs 7 kg.'}) + ev('done', {})\n"
        "    elif q == 'fam q':\n"
        "        t = ev('meta', {'role': 'clarify', 'family_options': [{'label': 'NV9 USB', 'send': 'fam q (NV9 USB)'}, {'label': 'Not sure', 'send': 'x'}]}) + ev('delta', {'text': 'Which NV9?'})\n"
        "    elif q == 'boom':\n"
        "        t = ev('error', {'status': 503, 'detail': 'down'})\n"
        "    else:\n"
        "        t = ev('meta', {'role': 'fast', 'sources': [{'source': 'm.pdf', 'pages': [1]}]}) + ev('delta', {'text': 'plain answer.'})\n"
        "    return StreamingResponse(iter([t]), media_type='text/event-stream')\n"),
}


def fake_post(url, json=None, **_k):
    text = json["messages"][-1]["content"]
    labels = re.findall(r"ANSWER ([A-G]):\n(.*?)(?=\n\nANSWER [A-G]:|\n\nReply with JSON)", text, re.S)
    content = __import__("json").dumps({**{k: {"score": 3, "verdict": "x"} for k, a in labels}, "best": "tie", "why": "x"})
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


def mkrun(tmp, name, cases):
    run = os.path.join(tmp, name); os.makedirs(os.path.join(run, "manuals"))
    open(os.path.join(run, "manuals", "nv9usb.txt"), "w").write("[m.pdf p1] text")
    json.dump({"cases": cases}, open(os.path.join(run, "cases.json"), "w"))
    return run


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
        run = mkrun(tmp, "r", [
            {"id": "a", "q": "cmp q", "type": "compare", "expected": "answered", "reference": "r", "product": "nv9usb", "compare_with": ["bv30"]},
            {"id": "b", "q": "fam q", "type": "fact", "expected": "answered", "reference": "r", "product": "nv9usb"},
            {"id": "c", "q": "plain", "turn1": "first", "type": "fact", "expected": "answered", "reference": "r", "product": "nv9usb"}])
        assert be(run, "system", "normal", "--path", "stream")[0] == 0
        assert state(run)["path"] == "stream"
        calls = sys.modules["main"].CALLS
        assert all(c[2].startswith("Bearer tok-") for c in calls), calls
        qs = [c[0] for c in calls]
        assert qs.index("first") < qs.index("plain") and qs.index("fam q") < qs.index("fam q (NV9 USB)"), qs
        assert ("fam q (NV9 USB)", "nv9usb") == calls[qs.index("fam q (NV9 USB)")][:2], calls
        rows = state(run)["sys_normal"]
        a = rows["a"]
        assert a["steps"]["fired"] and a["steps"]["compared"] and len(a["steps"]["parts"]) == 2, a
        assert a["steps"]["unsupported_figures"] == ["7"], a["steps"]
        assert a["answer"].startswith("### Comparison"), a
        assert rows["b"]["answer"] == "plain answer." and rows["b"]["family_ask"]["tapped"] == "NV9 USB", rows["b"]
        assert "steps" not in rows["c"], rows["c"]
        assert be(run, "grade")[0] == 0
        assert state(run)["summary"]["sys_normal"]["stepwise"] == {"fired": 1, "compared": 1, "unsupported_figures": 1, "cases_unsupported": ["a"]}
        code = be(run, "system", "normal")[0]
        assert code and "--path stream" in str(code), code
        assert be(run, "reset")[0] == 0 and "path" not in state(run)
        run3 = mkrun(tmp, "r3", [{"id": "z", "q": "boom", "type": "fact", "expected": "answered", "reference": "r", "product": "nv9usb"}])
        code = be(run3, "system", "normal", "--path", "stream")[0]
        assert code and "503" in str(code), code
        assert not state(run3).get("sys_normal"), "a row was saved for an error"
        print("ok")
    finally:
        os.chdir(HERE)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
