"""Self-check for the picture test (D-20261022-14): blind_eval.py's figure marks and tools/image_set.py, fake bot and grader, no network.

  python tools/test_images.py
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
sys.path.insert(0, HERE)
import image_set

STUB = {
    "torch.py": "",
    "logger.py": "LOG_FILE = None\n",
    "llm.py": "def _post(url, *a, **k): raise RuntimeError('no network')\n",
    "main.py": (
        "class QueryRequest:\n"
        "    def __init__(self, **k): self.__dict__.update(k)\n"
        "def _policy_setting(name, default): return False\n"
        "def _product_names(): return {}\n"
        "def _traced_query(req, _x, attach=True):\n"
        "    return {'answer': 'from m.pdf', 'sources': [{'source': 'm.pdf', 'pages': [1], 'figures': [{'name': 'p1_1.png'}]}]}\n"),
}


def fake_post(url, json=None, **_k):
    text = json["messages"][-1]["content"]
    labels = re.findall(r"ANSWER ([A-G]):\n(.*?)(?=\n\nANSWER [A-G]:|\n\nReply with JSON)", text, re.S)
    score = 0 if "WRONG" in text else 3
    content = __import__("json").dumps({**{k: {"score": score, "verdict": "x"} for k, a in labels}, "best": "tie", "why": "x"})
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
        ops = os.path.join(tmp, "ops")
        os.environ.update(GO_CODE=src, GO_DATA=src, LAB_OPS=ops, GO_LAB_LOCK_HELD="1",
                          DEEPSEEK_API_KEY="test-not-a-key", LAB_PROVIDER="deepseek")
        base = {"src": "images", "type": "figure", "expected": "answered", "product": "p", "source": "m.pdf", "reference": "r"}
        cases = [{**base, "id": "a", "figure": "p1_1.png", "q": "q a"},
                 {**base, "id": "b", "figure": "p2_1.png", "q": "q b"},
                 {**base, "id": "c", "figure": ["p9_9.png", "p1_1.png"], "q": "WRONG c"}]

        def fresh(parent, name):
            run = os.path.join(parent, name); os.makedirs(os.path.join(run, "manuals"))
            open(os.path.join(run, "manuals", "p.txt"), "w").write("[m.pdf p1] text about 99 mm\n\n[m.pdf p2] more")
            json.dump({"cases": cases}, open(os.path.join(run, "cases.json"), "w"))
            return run

        run = fresh(os.path.join(ops, "heldout", "images"), "x")
        assert be(run, "system", "normal")[0] == 0
        assert all(r["figures"] == {"m.pdf": ["p1_1.png"]} for r in state(run)["sys_normal"].values())
        assert be(run, "grade")[0] == 0
        st = state(run)
        assert st["summary"]["sys_normal"]["images"] == {"n": 3, "figure_found": 2, "explanation_right": 2, "found": ["a", "c"], "right": ["a", "b"]}, st["summary"]["sys_normal"]["images"]
        assert st["grades"]["b"]["sys_normal"]["figure_found"] is False

        loose = fresh(os.path.join(tmp, "runs"), "x")
        code = be(loose, "system", "normal")[0]
        assert isinstance(code, str) and "heldout" in code, code

        figs = os.path.join(tmp, "figs")
        for doc, n in (("m.pdf", 60), ("n.pdf", 3)):
            os.makedirs(os.path.join(figs, doc))
            json.dump({"figures": {f"p{i}_1.png": {} for i in range(n)}}, open(os.path.join(figs, doc, "figures.json"), "w"))
        for f in ("p1_1.png", "p2_1.png"):
            open(os.path.join(figs, "m.pdf", f), "w").close()
        r2 = os.path.join(tmp, "r2", "stamp"); os.makedirs(os.path.join(r2, "manuals")); os.makedirs(os.path.join(r2, "parts"))
        open(os.path.join(r2, "manuals", "a.txt"), "w").write("[m.pdf p1] text about 99 mm\n\n[n.pdf p1] x")
        open(os.path.join(r2, "manuals", "b.txt"), "w").write("[m.pdf p2] more")
        assert image_set.plan(r2, figs) == ["a | n.pdf=1", "b | m.pdf=2"], image_set.plan(r2, figs)
        good = {"product": "p", "source": "m.pdf", "figure": "p1_1.png", "q": "q", "reference": "r"}
        json.dump({"cases": [{**good, "id": "z1", "must_include": ["99 mm"]}, {**good, "id": "z2", "must_include": ["SH4"]}]},
                  open(os.path.join(r2, "parts", "p.json"), "w"))
        assert image_set.merge(r2, figs)[-1] == "OK 1"
        kept = json.load(open(os.path.join(r2, "cases.json")))["cases"]
        assert len(kept) == 1 and kept[0]["id"] == "Istamp-01" and kept[0]["src"] == "images" and kept[0]["pages"] == ["m.pdf p1"], kept
        json.dump({"cases": [{k: v for k, v in good.items() if k != "figure"} | {"id": "z3", "must_include": ["x"]}]},
                  open(os.path.join(r2, "parts", "p.json"), "w"))
        assert image_set.merge(r2, figs)[-1].startswith("FAIL")

        out = os.path.join(tmp, "out")
        image_set.score(run, out)
        sc = json.load(open(os.path.join(out, "score.json")))
        assert sc["by_product"] == {"p": {"n": 3, "figure_found": 2, "explanation_right": 2}}, sc["by_product"]
        print("ok")
    finally:
        os.chdir(HERE)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
