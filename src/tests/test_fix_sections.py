"""fix_sections (D-20261029-03): the whole fault-clearing section a ranking found.

The collection is faked, as in test_procedure_completion.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import retrieval_db  # noqa: E402

DOC = "BV30 Manual-v1.pdf"
ROWS = [
    (1, "CASHBOX TEXT", "Options › Cashbox Options"),
    (2, "jam a", "Maintenance › Clearing a Jam"),
    (3, "jam b", "Maintenance › Clearing a Jam"),
    (4, "jam c", "Maintenance › Clearing a Jam"),
    (5, "jam d", "Maintenance › Clearing a Jam"),
    (6, "ESCROW TEXT", "Options › Escrow Control"),
    (7, "later jam", "Maintenance › Clearing a Jam"),
]


class Fake:
    def __init__(self, source=DOC, boom=False):
        self.source, self.boom = source, boom

    def get(self, where=None, **kw):
        if self.boom:
            raise RuntimeError("down")
        src = (where or {}).get("source")
        if src != self.source:
            return {"ids": [], "documents": [], "metadatas": []}
        return {"ids": [f"{src}:h:{n}" for n, _, _ in ROWS],
                "documents": [t for _, t, _ in ROWS],
                "metadatas": [{"source": src, "page": n, "section": s} for n, _, s in ROWS]}


def use(source=DOC, boom=False):
    retrieval_db.get_collection = lambda: Fake(source, boom)


def hit(n, score=0.5, source=DOC):
    _, text, sec = ROWS[n - 1]
    return {"id": f"{source}:h:{n}", "text": text, "source": source, "page": n,
            "section": sec, "rerank_score": score, "retrieval_score": 0.1}


def test_whole_run_in_order_and_retrieved_dict_kept():
    use()
    r = hit(3)
    out = retrieval_db.fix_sections([hit(1, 0.98), r])
    assert [c["text"] for c in out] == ["jam a", "jam b", "jam c", "jam d"]
    assert out[1] is r and out[1]["rerank_score"] == 0.5
    assert [c.get("fetched_by") for c in out] == ["fix_section", None, "fix_section", "fix_section"]


def test_background_heading_never_picked():
    use()
    assert retrieval_db.fix_sections([hit(1, 0.98), hit(6, 0.9)]) == []


def test_below_floor_and_secondary_source_and_empty():
    use()
    assert retrieval_db.fix_sections([hit(3, 0.1)]) == []
    sec = "X Integration Test Script-v1.pdf"
    use(sec)
    assert retrieval_db.fix_sections([hit(3, 0.9, sec)]) == []
    assert retrieval_db.fix_sections([]) == []


def test_same_section_twice_gives_one_run_and_later_run_not_glued():
    use()
    out = retrieval_db.fix_sections([hit(3), hit(4)])
    assert [c["text"] for c in out] == ["jam a", "jam b", "jam c", "jam d"]
    out = retrieval_db.fix_sections([hit(7)])
    assert [c["text"] for c in out] == ["later jam"]


def test_max_two_sections():
    use()
    a, b, c = hit(3), hit(7), hit(3)
    b2 = dict(hit(7), section="Maintenance › Fault Finding")
    c2 = dict(hit(7), section="Maintenance › Coin Jam", id="x:h:99")
    out = retrieval_db.fix_sections([a, b2, c2])
    assert len({x["section"] for x in out}) <= 2


def test_budget_too_small_gives_retrieved_chunk_only():
    use()
    r = hit(3)
    assert retrieval_db.fix_sections([r], budget=5) == [r]


def test_collection_error_gives_empty():
    use(boom=True)
    assert retrieval_db.fix_sections([hit(3)]) == []


KB = "I can not find my device on IMS Local-.txt"


class FakeKB:
    def __init__(self, source):
        self.source = source

    def get(self, where=None, **kw):
        if (where or {}).get("source") != self.source:
            return {"ids": [], "documents": [], "metadatas": []}
        return {"ids": [f"{self.source}:h:{n}" for n in (1, 2)],
                "documents": ["rndis a", "rndis b"],
                "metadatas": [{"source": self.source, "page": 1, "section": ""}] * 2}


def kb_hit(source=KB, score=0.21, n=2):
    return {"id": f"{source}:h:{n}", "text": "x", "source": source, "page": 1,
            "section": "", "rerank_score": score, "retrieval_score": 0.1}


def test_symptom_titled_page_without_headings_is_a_fault_section():
    retrieval_db.get_collection = lambda: FakeKB(KB)
    out = retrieval_db.fix_sections([kb_hit()])
    assert [c["text"] for c in out] == ["rndis a", "x"]


def test_plain_titled_page_without_headings_is_not():
    src = "ICU Range User Manual.txt"
    retrieval_db.get_collection = lambda: FakeKB(src)
    assert retrieval_db.fix_sections([kb_hit(src, 0.9)]) == []


def test_symptom_titled_page_below_floor_or_secondary_is_not():
    retrieval_db.get_collection = lambda: FakeKB(KB)
    assert retrieval_db.fix_sections([kb_hit(score=0.1)]) == []
    ts = "NV200 Test Script - Software Error Handling.txt"
    retrieval_db.get_collection = lambda: FakeKB(ts)
    assert retrieval_db.fix_sections([kb_hit(ts, 0.9)]) == []


def test_symptom_titled_page_needs_titles():
    retrieval_db.get_collection = lambda: FakeKB(KB)
    assert retrieval_db.fix_sections([kb_hit()], titles=False) == []
