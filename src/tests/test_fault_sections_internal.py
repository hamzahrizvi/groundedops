"""The vague-fault checklist leaves out hidden documents (D-20261104-02)."""
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMP = tempfile.mkdtemp()
os.environ["INTERNAL_SOURCES_PATH"] = os.path.join(_TMP, "internal_sources.json")

import clarify  # noqa: E402
import db  # noqa: E402
import retrieval_db as rdb  # noqa: E402


class _Col:
    def count(self):
        return 4


CHUNKS = [
    {"text": "Check the supply.", "source": "XP100 Manual.pdf", "product": "xp100",
     "section": "Fault Finding › Error Codes", "page": 30},
    {"text": "Service only: reset the board.", "source": "XP100 Service Note.pdf",
     "product": "xp100", "section": "Troubleshooting", "page": 4},
    {"text": "Service only: error 7 is the board.", "source": "XP100 Service Note.pdf",
     "product": "xp100", "section": "Fault Finding › Error Codes", "page": 9},
    {"text": "Gateway error map.", "source": "icu_network_api spec.pdf", "product": "xp100",
     "section": "Error Handling", "page": 12},
]
for _p in (patch.object(db, "get_collection", return_value=_Col()),
           patch.object(rdb, "_get_bm25_index", return_value=(None, CHUNKS)),
           patch.object(rdb, "_drop_marked_chunks", lambda keys: None)):
    _p.start()

BOTH = ["Fault Finding: Error Codes", "Troubleshooting"]


def headings(results=()):
    return [s["heading"] for s in clarify.fault_sections("xp100", list(results))]


def _fault_chunks():
    return next(s for s in clarify.fault_sections("xp100", [])
                if s["heading"] == "Fault Finding: Error Codes")["chunks"]


def test_an_internal_document_gives_no_heading_and_no_source():
    assert headings() == BOTH
    rdb.set_internal(["XP100 Service Note.pdf"], True, copies=False)
    try:
        assert headings() == ["Fault Finding: Error Codes"]
        assert [c["source"] for c in _fault_chunks()] == ["XP100 Manual.pdf"]
    finally:
        rdb.set_internal(["XP100 Service Note.pdf"], False, reindex=False)
    assert headings() == BOTH
    assert len(_fault_chunks()) == 2


def test_an_excluded_sources_document_is_left_out_too():
    assert "Error Handling" not in headings()


def test_a_retrieved_hidden_chunk_is_skipped():
    assert "Error Handling" not in headings([CHUNKS[3]])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
