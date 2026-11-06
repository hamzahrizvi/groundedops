"""Self-check for tools/page_reach.py case_set: pages_now wins over pages, gold_gone is skipped,
a question seen in two runs is one entry.

  python tools/lab_lock.py python tools/test_page_reach.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from page_reach import case_set, gold_pages, internal_gold, reach_flags, resolve_name, supervise  # noqa: E402


def case(i, q, **kw):
    return {"id": i, "q": q, "type": "lookup", "product": "p", "expected": "answered", **kw}


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
    print("ok")


if __name__ == "__main__":
    main()
