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
from page_reach import case_set, gold_pages  # noqa: E402


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
    print("ok")


if __name__ == "__main__":
    main()
