"""The spec index reads a PDF again only when that PDF changed.

Filing and the internal mark are applied over cached per-file rows: on
2026-10-23 any change re-read all 543 PDFs (618.7s) and the console's
"Product specifications" stage, and any visitor asking a spec question
meanwhile, waited for it.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMP = tempfile.mkdtemp()
os.environ["INTERNAL_SOURCES_PATH"] = os.path.join(_TMP, "internal_sources.json")

import retrieval_db as rdb  # noqa: E402
import sales  # noqa: E402

READS: list[str] = []


def _fake_extract(path):
    READS.append(os.path.basename(path))
    return [{"table": "Power", "attribute": "Voltage",
             "values": [os.path.basename(path)], "page": 1}]


sales._extract = _fake_extract


def _setup():
    """A documents folder with two PDFs and a fresh process's empty caches."""
    d = tempfile.mkdtemp(dir=_TMP)
    for i, f in enumerate(("A.pdf", "B.pdf")):
        open(os.path.join(d, f), "wb").close()
        os.utime(os.path.join(d, f), (1000 + i, 1000 + i))
    sales._INDEX_CACHE = os.path.join(d, "spec_index.json")
    _restart()
    READS.clear()
    return d


def _restart():
    sales._INDEX = sales._INDEX_KEY = sales._FILES = None


FILED = {"A.pdf": ("bv30", "BV30", "Note Validator"),
         "B.pdf": ("nv9", "NV9", "Note Validator")}


def test_the_first_build_reads_each_pdf_once_and_a_restart_reads_none():
    d = _setup()
    rows = sales.get_index(d, FILED)
    assert sorted(READS) == ["A.pdf", "B.pdf"]
    assert {(r["source"], r["product"]) for r in rows} == {("A.pdf", "bv30"), ("B.pdf", "nv9")}
    READS.clear()
    _restart()
    assert sales.get_index(d, FILED) == rows
    assert READS == []


def test_marking_internal_reads_nothing_and_drops_its_rows():
    d = _setup()
    sales.get_index(d, FILED)
    READS.clear()
    rdb.set_internal(["A.pdf"], True)
    try:
        rows = sales.get_index(d, FILED)
        assert {r["source"] for r in rows} == {"B.pdf"}
        _restart()                                   # and after a restart
        assert {r["source"] for r in sales.get_index(d, FILED)} == {"B.pdf"}
    finally:
        rdb.set_internal(["A.pdf"], False)
    assert {r["source"] for r in sales.get_index(d, FILED)} == {"A.pdf", "B.pdf"}
    assert READS == []


def test_an_internal_document_is_never_read():
    d = _setup()
    rdb.set_internal(["A.pdf"], True)
    try:
        rows = sales.get_index(d, FILED)
    finally:
        rdb.set_internal(["A.pdf"], False)
    assert READS == ["B.pdf"]
    assert {r["source"] for r in rows} == {"B.pdf"}


def test_refiling_reads_nothing_and_moves_the_rows():
    d = _setup()
    sales.get_index(d, FILED)
    READS.clear()
    rows = sales.get_index(d, {**FILED, "A.pdf": ("mycheckr", "myCheckr", "Coin")})
    assert {(r["source"], r["product"]) for r in rows} == {("A.pdf", "mycheckr"), ("B.pdf", "nv9")}
    rows = sales.get_index(d, {"B.pdf": FILED["B.pdf"]})          # unfiled: skipped
    assert {r["source"] for r in rows} == {"B.pdf"}
    sales.get_index(d, FILED)                                      # filed again: still cached
    assert READS == []


def test_a_changed_or_new_pdf_is_the_only_one_read():
    d = _setup()
    sales.get_index(d, FILED)
    READS.clear()
    os.utime(os.path.join(d, "B.pdf"), (2000, 2000))
    sales.get_index(d, FILED)
    assert READS == ["B.pdf"]
    READS.clear()
    open(os.path.join(d, "C.pdf"), "wb").close()
    rows = sales.get_index(d, {**FILED, "C.pdf": ("bv50", "BV50", "Note Validator")})
    assert READS == ["C.pdf"]
    assert {r["source"] for r in rows} == {"A.pdf", "B.pdf", "C.pdf"}


def test_a_removed_pdf_leaves_the_cache():
    d = _setup()
    sales.get_index(d, FILED)
    os.remove(os.path.join(d, "A.pdf"))
    assert {r["source"] for r in sales.get_index(d, FILED)} == {"B.pdf"}
    with open(sales._INDEX_CACHE, encoding="utf-8") as f:
        assert set(json.load(f)["files"]) == {"B.pdf"}


def test_the_old_cache_format_converts_without_reading_when_still_current():
    d = _setup()
    old_rows = [{"product": "bv30", "product_name": "BV30", "category": "Note Validator",
                 "table": "Power", "attribute": "Voltage", "values": ["12V"],
                 "source": "A.pdf", "page": 3}]          # B.pdf had no tables
    with open(sales._INDEX_CACHE, "w", encoding="utf-8") as f:
        json.dump({"key": sales._fingerprint(d, FILED), "rows": old_rows}, f)
    assert sales.get_index(d, FILED) == old_rows
    assert READS == []
    # a stale old cache is just a miss
    with open(sales._INDEX_CACHE, "w", encoding="utf-8") as f:
        json.dump({"key": "stale", "rows": old_rows}, f)
    _restart()
    sales.get_index(d, FILED)
    assert sorted(READS) == ["A.pdf", "B.pdf"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
