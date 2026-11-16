"""Self-check for tools/page_reach.py case_set, gold_reach and failure_cause: pages_now wins over pages, gold_gone is skipped,
a question seen in two runs is one entry.

  python tools/lab_lock.py python tools/test_page_reach.py
"""
import contextlib
import io
import json
import os
import runpy
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from page_reach import answer_terms, case_set, failure_cause, gold_pages, gold_reach, holds, internal_gold, passage_reach, reach_flags, resolve_name, supervise  # noqa: E402


def case(i, q, **kw):
    return {"id": i, "q": q, "type": "lookup", "product": "p", "expected": "answered", **kw}


def blind_eval_on(count):
    """blind_eval's system phase on a stub src whose index holds `count` passages: (exit code, questions asked, rows)."""
    tmp, env, cwd = tempfile.mkdtemp(), dict(os.environ), os.getcwd()
    try:
        src = os.path.join(tmp, "src"); os.makedirs(src)
        stub = {"torch.py": "", "logger.py": "LOG_FILE = None\n",
                "llm.py": "def _post(url, *a, **k): raise RuntimeError('no network')\n",
                "db.py": ("import os\nCHROMA_DIR = os.path.dirname(os.path.abspath(__file__))\n"
                          f"class _C:\n    def count(self): return {count}\n"
                          "def get_collection(): return _C()\n"),
                "main.py": ("class QueryRequest:\n    def __init__(self, **k): self.__dict__.update(k)\n"
                            "def _policy_setting(name, default): return False\ndef _product_names(): return {}\nASKED = []\n"
                            "def _traced_query(req, _x, attach=True):\n    ASKED.append(req.q); return {'answer': 'a'}\n")}
        for f, body in stub.items():
            open(os.path.join(src, f), "w").write(body)
        run = os.path.join(tmp, "r"); os.makedirs(run)
        json.dump({"cases": [case("a", "qa", reference="r")]}, open(os.path.join(run, "cases.json"), "w"))
        os.environ.update(GO_CODE=src, GO_DATA=src, LAB_OPS=os.path.join(tmp, "ops"), GO_LAB_LOCK_HELD="1", REPEAT="1",
                          SYSTEMS="sys_normal", DEEPSEEK_API_KEY="test-not-a-key", LAB_PROVIDER="deepseek")
        for m in ("main", "db", "llm", "logger", "torch", "pipeline_trace"): sys.modules.pop(m, None)
        sys.argv, code = [os.path.join(HERE, "blind_eval.py"), run, "system", "normal"], 0
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(sys.argv[0], run_name="__main__")
        except SystemExit as e:
            code = e.code or 0
        st = os.path.join(run, "cases_state.json")
        rows = json.load(open(st, encoding="utf-8")).get("sys_normal", {}) if os.path.exists(st) else {}
        return code, list(sys.modules["main"].ASKED), rows
    finally:
        os.chdir(cwd); os.environ.clear(); os.environ.update(env)
        for m in ("main", "db", "llm", "logger", "torch"): sys.modules.pop(m, None)
        shutil.rmtree(tmp, ignore_errors=True)


def empty_index_guard():
    code, asked, _ = blind_eval_on(0)
    assert "holds no passages" in str(code) and asked == [], (code, asked)   # stops before asking anything
    code, asked, rows = blind_eval_on(3)
    assert code == 0 and asked == ["qa"], (code, asked)
    assert rows["a"]["reach_level"] == "page", rows   # stub has no retrieval_db: reach is an error row, level page


def main():
    with tempfile.TemporaryDirectory() as t:
        a = case("a", "qa", pages=["A.txt p1"], pages_now=["A.pdf p3"])
        runs = {"r1": [a, case("b", "qb", pages=["B.txt p1"], gold_gone="x"), case("c", "qc", pages=["B.pdf p2"])],
                "r2": [dict(a, id="a2")]}
        for r, cs in runs.items():
            os.makedirs(os.path.join(t, "runs", r))
            json.dump({"cases": cs}, open(os.path.join(t, "runs", r, "cases.json"), "w"))
        got = case_set(ops=t)
        assert len(got) == 2, got
        ga = next(g for g in got if g["q"] == "qa")
        assert ga["gold"] == [("A.pdf", "3")] and [s[0] for s in ga["seen_in"]] == ["r1", "r2"], ga
        assert all(g["q"] != "qb" for g in got)
    assert gold_pages({"pages": ["A.txt p1"]}) == [("A.txt", "1")]
    assert gold_pages({"pages": ["Q p6, p8, p10"]}) == [("Q", "6"), ("Q", "8"), ("Q", "10")]
    assert gold_pages({"pages": ["C p1-p2", "p5", "X.pdf p3 (Card)"]}) == [("C", "1"), ("C", "2"), ("C", "5"), ("X.pdf", "3")]
    assert gold_pages({"pages": ["NV200 Spectral SSP Manual v.1 p88"]}) == [("NV200 Spectral SSP Manual v.1", "88")]
    qs = "CS-MyConnect Quick Start Guide – Installer Edition-240226-141456.pdf"
    S = {qs, "CS-MyCheckr Installation & MyConnect App Pre-Requisites Checklist-170925-134345.pdf", "C-SCS  - Sales Guide.txt",
         "NV200 Spectral SSP Manual v.1.pdf", "MyCheckr Mini User Manual-v5.pdf", "A.pdf", "A.txt", "Manual X-v1.pdf", "Manual X-v2.pdf"}
    assert resolve_name("CS-MyConnect Quick Start Guide - Installer Edition", S) == (qs, "suffix")
    assert resolve_name("CS-MyConnect Quick Start Guide", S) == (qs, "prefix")
    assert resolve_name("CS-MyCheckr Installation & MyConnect App Pre-Requisites Checklist", S)[0].endswith("134345.pdf")
    assert resolve_name("C-SCS - Sales Guide.txt", S) == ("C-SCS  - Sales Guide.txt", "name")
    assert resolve_name("NV200 Spectral SSP Manual v.1", S) == ("NV200 Spectral SSP Manual v.1.pdf", "name")
    assert resolve_name("MyCheckr Mini User Manual.txt", S) == (None, None)   # a .txt never becomes a PDF
    assert resolve_name("A", S)[0] is None and resolve_name("Manual X", S)[0] is None   # two hits: ambiguous
    assert resolve_name("Old Name", S, {"old name": "A.pdf"}) == ("A.pdf", "table")
    assert resolve_name("A.pdf", S, copy_of={"A.pdf": "A.txt"}) == ("A.txt", "exact")
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "r.json")
        code = ("import os,sys\no=sys.argv[1]\nif not os.path.exists(o+'.flag'):\n"
                " open(o+'.flag','w').close(); open(o+'.current','w').write('k1'); sys.exit(3)\n")
        assert supervise([sys.executable, "-c", code, out], out) == 0          # crashed on k1, restarted, finished
        assert json.load(open(out + ".crashes.json")) == {"k1": 1}
        assert supervise([sys.executable, "-c", "import sys; sys.exit(4)"], out) == 4   # died before a question: no restart
    assert internal_gold(["A.pdf p1", "ICU_Age_Result_Quick_Guide_v1_1.pdf p2"], lambda s: "icu_age" in s.lower()) == ["ICU_Age_Result_Quick_Guide_v1_1.pdf p2"]
    g = ["A p1", "I p2"]
    assert reach_flags(g, g, ["I p2"], ["A p1"]) == (False, True)
    assert reach_flags(g, g, [], ["A p1"]) == (False, False)
    idx = {("A.pdf", "3"), ("A.pdf", "4"), ("B-v2.pdf", "1")}
    srcs = {"A.pdf", "B-v2.pdf"}
    tr = lambda got, kept: {"stages": [{"id": "retrieve"}], "meta": {"pages_retrieved": got, "pages_prompt": kept}}
    one = case("x", "q", pages=["A.pdf p3"])
    assert gold_reach(one, tr({"A.pdf": [3]}, {"A.pdf": [3]}), idx, srcs) == "prompt"
    assert gold_reach(one, tr({"A.pdf": [3]}, {"A.pdf": [4]}), idx, srcs) == "cut"
    assert gold_reach(one, tr({"A.pdf": [4]}, {}), idx, srcs) == "never"
    assert gold_reach(one, {"stages": [{"id": "sales"}], "meta": {}}, idx, srcs) == "never"   # never searched
    assert gold_reach(one, {"stages": [{"id": "retrieve"}], "meta": {}}, idx, srcs) == "no_trace"   # old code
    assert gold_reach(one, None, idx, srcs) == "no_trace"
    assert gold_reach(case("y", "q"), tr({}, {}), idx, srcs) == "no_gold"
    assert gold_reach(case("z", "q", pages=["A.pdf p9"]), tr({}, {}), idx, srcs) == "stale"
    assert gold_reach(case("w", "q", pages=["B.pdf p1"]), tr({"B-v2.pdf": [1]}, {"B-v2.pdf": [1]}), idx, srcs) == "prompt"   # renamed
    two = case("m", "q", pages=["A.pdf p3", "B.pdf p1"])
    assert gold_reach(two, tr({"A.pdf": [3], "B-v2.pdf": [1]}, {"A.pdf": [3]}), idx, srcs) == "cut"
    assert gold_reach(two, tr({"A.pdf": [3]}, {"A.pdf": [3]}), idx, srcs) == "never"
    assert failure_cause("prompt", "safe_decline") == "found_and_declined"
    assert failure_cause("prompt", "wrong") == failure_cause("prompt", "partial") == "found_and_misread"
    assert failure_cause("cut", "wrong") == "found_and_cut" and failure_cause("never", "partial") == "never_found"
    assert failure_cause("no_gold", "wrong") == "no_page"
    assert all(failure_cause(r, "wrong") == "unknown" for r in (None, "stale", "no_trace", "error: X"))
    # D-20261115-02: per passage. Page A.pdf p3 has two passages; only the second holds the answer.
    rel = case("r", "q", pages=["A.pdf p3"], must_include=["8 characters"])
    ix = {("A.pdf", "3"): ["changelog, other fixes", "minimum password length of 8 characters"], ("A.pdf", "4"): ["x"]}
    wrong, right, other = ("A.pdf", 3, "changelog, other fixes"), ("A.pdf", 3, "Minimum password length of 8\ncharacters."), ("A.pdf", 4, "x")
    P = lambda got, kept, i=ix: {"pages_retrieved": got, "pages_prompt": kept, "index": i}
    assert gold_reach(rel, tr({"A.pdf": [3]}, {}), idx, srcs) == "cut"   # the page reading
    assert passage_reach(rel, tr({"A.pdf": [3]}, {}), idx, srcs, passages=P([wrong], [])) == ("never", "passage")   # wrong passage ranked
    assert passage_reach(rel, tr({"A.pdf": [3]}, {"A.pdf": [3]}), idx, srcs, passages=P([wrong], [wrong])) == ("never", "passage")
    assert passage_reach(rel, tr({"A.pdf": [3, 4]}, {"A.pdf": [4]}), idx, srcs, passages=P([wrong, right, other], [other])) == ("cut", "passage")   # right passage ranked, then cut
    assert passage_reach(rel, tr({"A.pdf": [3]}, {"A.pdf": [3]}), idx, srcs, passages=P([right], [right])) == ("prompt", "passage")
    assert passage_reach(case("b2", "q", pages=["A.pdf p3"], reference="no quote"), tr({"A.pdf": [3]}, {}), idx, srcs, passages=P([wrong], [])) == ("cut", "page")   # nothing to match on
    assert passage_reach(case("s", "q", pages=["A.pdf p3"], must_include=["eight characters"]), tr({"A.pdf": [3]}, {}), idx, srcs, passages=P([wrong], [])) == ("cut", "page")   # no passage states it so
    assert passage_reach(rel, tr({"A.pdf": [3]}, {}), idx, srcs, passages=P([("A.pdf", 3, "one")], [], {("A.pdf", "3"): ["one"]})) == ("cut", "passage")   # one passage = the page
    assert passage_reach(rel, tr({"A.pdf": [3]}, {}), idx, srcs) == ("cut", "page")   # no passages kept (old row, stream path)
    assert passage_reach(rel, tr({"A.pdf": [4]}, {}), idx, srcs, passages=P([other], [])) == ("never", "passage")
    assert answer_terms({"reference": 'Notes: "minimum password length of 8 characters." So no.'}) == ["minimum password length of 8 characters."]
    assert answer_terms({"must_include": ["7", " "], "reference": '"a quoted span here"'}) == ["7"]
    assert holds("Username length <= 50 and >= 7", ["7", "50"]) and not holds("70 items in 500 ms", ["7", "50"])
    empty_index_guard()
    print("ok")


if __name__ == "__main__":
    main()
