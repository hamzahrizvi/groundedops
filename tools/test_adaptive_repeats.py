"""Self-check for blind_eval.py ADAPTIVE=1 (the CEO, 2026-10-21) with a fake bot and a fake grader: no network.

  python tools/test_adaptive_repeats.py

Three questions, weekly_blind.sh's order: "good" is right first time, "late" only from repeat 2, "bad" never.
1. repeats 2-3 ask only late and bad; good's later answers and grades are copied at no cost;
2. the whole manual is asked once, only for bad; good and late get not_asked rows marked 3 without a grader call;
3. bad's final mark is graded beside its baseline answer, and the cost of its first mark is kept;
4. without ADAPTIVE every question is asked every time, as before;
5. a gate (tools/gate_run.sh's order): "fixed" is wrong on the base code and right on the branch. Repeat 2 asks the
   base side again only for what its repeat 1 missed (ADAPTIVE_FROM after/:sys_before) and the branch side only for
   its own misses; grading repeat 2 marks only the arms asked again, copied arms and the baseline keep repeat 1's mark.
"""
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
calls = {"bot": [], "baseline": [], "grader": []}

STUB = {
    "torch.py": "",
    "logger.py": "LOG_FILE = None\n",
    "llm.py": "def _post(url, *a, **k): raise RuntimeError('no network')\n",
    "main.py": (
        "import os\n"
        "class QueryRequest:\n"
        "    def __init__(self, **k): self.__dict__.update(k)\n"
        "def _policy_setting(name, default): return False\n"
        "def _product_names(): return {}\n"
        "CALLS = []\n"
        "def _traced_query(req, _x, attach=True):\n"
        "    CALLS.append((os.environ.get('REPEAT', '1'), req.q))\n"
        "    return {'answer': req.q + ' ' + os.environ.get('SIDE', '') + ' r' + os.environ.get('REPEAT', '1')}\n"),
}


def fake_post(url, json=None, **_k):
    text = json["messages"][-1]["content"]
    if text.startswith("You are grading"):
        calls["grader"].append(text)
        labels = re.findall(r"ANSWER ([A-G]):\n(.*?)(?=\n\nANSWER [A-G]:|\n\nReply with JSON)", text, re.S)
        def mark(a):
            ok = "good" in a or "BASE" in a or ("late" in a and " r1" not in a) or ("fixed" in a and "branch" in a)
            return {"score": 3 if ok else 1, "verdict": "correct" if ok else "safe_decline"}
        content = __import__("json").dumps({**{k: mark(a) for k, a in labels}, "best": "tie", "why": "x"})
    else:
        calls["baseline"].append(text); content = "BASE answer"
    return types.SimpleNamespace(raise_for_status=lambda: None,
                                 json=lambda: {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 10}})


def be(run, *argv, repeat=1, systems="sys_normal,baseline"):
    os.environ.update(REPEAT=str(repeat), SYSTEMS=systems)
    sys.argv = [BE, run, *argv]
    try:
        runpy.run_path(BE, run_name="__main__")
    except SystemExit as e:
        assert not e.code, (argv, e.code)


def state(run, k=1):
    return json.load(open(os.path.join(run, "cases_state.json" if k == 1 else f"cases_state.r{k}.json"), encoding="utf-8"))


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
        cases = [{"id": i, "q": i, "type": "fact", "expected": "answered", "reference": "r", "product": "p"} for i in ("good", "late", "bad")]

        def fresh(name):
            run = os.path.join(tmp, name); os.makedirs(os.path.join(run, "manuals"))
            open(os.path.join(run, "manuals", "p.txt"), "w").write("[m.pdf p1] text")
            json.dump({"cases": cases}, open(os.path.join(run, "cases.json"), "w"))
            return run

        run = fresh("adaptive"); os.environ["ADAPTIVE"] = "1"
        be(run, "system", "normal", systems="sys_normal"); be(run, "grade", systems="sys_normal")
        for k in (2, 3):
            be(run, "system", "normal", repeat=k, systems="sys_normal"); be(run, "grade", repeat=k, systems="sys_normal")
        be(run, "baseline"); be(run, "grade")
        bot = sys.modules["main"].CALLS
        assert [q for k, q in bot if k == "1"] == ["good", "late", "bad"], bot
        assert [q for k, q in bot if k in "23"] == ["late", "bad", "late", "bad"], bot                 # 1
        r2 = state(run, 2)
        assert r2["sys_normal"]["good"]["copied_from_r1"] and r2["sys_normal"]["good"]["usd"] == 0
        assert r2["grades"]["good"]["copied_from_r1"] and r2["grades"]["good"]["usage"]["usd"] == 0
        assert r2["grades"]["late"]["sys_normal"]["score"] == 3 and r2["grades"]["bad"]["sys_normal"]["score"] == 1
        assert len(calls["baseline"]) == 1, calls["baseline"]                                             # 2
        s1 = state(run)
        assert s1["baseline"]["good"]["not_asked"] and s1["baseline"]["late"]["not_asked"] and not s1["baseline"]["bad"].get("not_asked")
        assert s1["grades"]["good"]["baseline"] == {"score": 3, "verdict": "not_asked"} == s1["grades"]["late"]["baseline"]
        assert len(calls["grader"]) == 3 + 2 + 2 + 1, len(calls["grader"])                                # r1, r2, r3, bad again
        assert calls["grader"][-1].count("\nANSWER ") == 2 and "BASE" in calls["grader"][-1]              # 3
        assert s1["grades"]["bad"]["baseline"]["score"] == 3 and s1["grades"]["bad"]["sys_normal"]["score"] == 1
        assert s1["grades"]["bad"]["usage"]["usd"] > s1["grades"]["good"]["usage"]["usd"] > 0, s1["grades"]
        assert s1["summary"]["baseline"]["verdicts"] == {"correct": 1, "not_asked": 2}, s1["summary"]

        os.environ.pop("ADAPTIVE"); bot.clear(); calls["baseline"].clear(); calls["grader"].clear()
        run = fresh("plain")
        for k in (1, 2):
            be(run, "system", "normal", repeat=k)
        be(run, "baseline")
        for k in (1, 2):
            be(run, "grade", repeat=k)
        assert len(bot) == 6 and len(calls["baseline"]) == 3 and len(calls["grader"]) == 6, (bot, calls)   # 4
        assert all(calls["grader"][i].count("\nANSWER ") == 2 for i in range(6))
        os.environ["ADAPTIVE"] = "1"; bot.clear(); calls["grader"].clear()
        cases.append({"id": "fixed", "q": "fixed", "type": "fact", "expected": "answered", "reference": "r", "product": "p"})
        before, after = fresh("before"), fresh("after")
        st = {"baseline": {c["id"]: {"answer": "BASE answer", "wall": 1, "usd": 0.001} for c in cases}}
        for d in (before, after):
            json.dump(st, open(os.path.join(d, "cases_state.json"), "w"))
        def ride(k):   # gate_run.sh's ride: the before's answers into the after state as sys_before
            a = state(after, k); a["sys_before"] = state(before, k)["sys_normal"]
            json.dump(a, open(os.path.join(after, "cases_state.json" if k == 1 else f"cases_state.r{k}.json"), "w"))
        G = "sys_normal,baseline,sys_before"
        os.environ["SIDE"] = "base"; be(before, "system", "normal")
        os.environ["SIDE"] = "branch"; be(after, "system", "normal"); ride(1); be(after, "grade", systems=G)
        assert len(calls["grader"]) == 4 and all(t.count("\nANSWER ") == 3 for t in calls["grader"])
        os.environ.update(SIDE="base", ADAPTIVE_FROM=os.path.join(after, "cases_state.json") + ":sys_before")
        be(before, "system", "normal", repeat=2)
        os.environ.pop("ADAPTIVE_FROM"); os.environ["SIDE"] = "branch"; be(after, "system", "normal", repeat=2)
        assert sorted(q for k, q in bot if k == "2") == ["bad", "bad", "fixed", "late", "late"], bot   # base: late bad fixed
        ride(2); be(after, "grade", repeat=2, systems=G)                                                # branch: late bad
        r2, r1 = state(after, 2)["grades"], state(after)["grades"]
        assert len(calls["grader"]) == 4 + 3, len(calls["grader"])                                     # good costs no call
        assert r2["good"]["copied_from_r1"] and r2["good"]["sys_before"]["score"] == 3
        assert r2["fixed"]["sys_normal"]["score"] == 3 and r2["fixed"]["sys_before"]["score"] == 1
        assert r2["fixed"]["labels"] == {"A": "sys_before"}, r2["fixed"]                               # only the base side
        assert r2["late"]["sys_normal"]["score"] == 3 == r2["late"]["sys_before"]["score"] and len(r2["late"]["labels"]) == 2
        assert all(r2[c]["baseline"] == r1[c]["baseline"] for c in r2)                                # baseline: repeat 1's
        print("test_adaptive_repeats: OK")
    finally:
        os.chdir(HERE)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
