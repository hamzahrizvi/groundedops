"""Documents marked internal never answer a customer, and only those.

Marked by exact name, so marking "Manual.txt" must not hide "SSP Manual.txt"
the way an EXCLUDED_SOURCES substring would.
"""
import json
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMP = tempfile.mkdtemp()
os.environ["INTERNAL_SOURCES_PATH"] = os.path.join(_TMP, "internal_sources.json")

import retrieval_db as rdb  # noqa: E402
import sales  # noqa: E402
import jsonstore  # noqa: E402


def test_marked_by_exact_name_only():
    rdb.set_internal(["Manual.txt"], True)
    assert rdb.is_excluded("Manual.txt")
    assert not rdb.is_excluded("SSP Manual.txt")          # a substring would have caught it
    # the env substrings still apply as before
    assert rdb.is_excluded("ICU_Network_API-v1.0.50.pdf")
    rdb.set_internal(["Manual.txt"], False)
    assert not rdb.is_excluded("Manual.txt")


def test_customers_never_reach_an_internal_document():
    rdb.set_internal(["Field Trials.txt"], True)
    meta = {"source": "Field Trials.txt", "prod_mycheckr": True, "product": "mycheckr"}
    assert not rdb._matches_scope(meta, None, None)
    assert not rdb._matches_scope(meta, None, {"product": "mycheckr"})
    # staff naming the document itself (local-only /query) still reach it
    assert rdb._matches_scope(meta, "Field Trials.txt", None)
    rdb.set_internal(["Field Trials.txt"], False)
    assert rdb._matches_scope(meta, None, {"product": "mycheckr"})


def test_the_mark_survives_a_restart():
    rdb.set_internal(["Pulse Walkthrough.txt"], True)
    rdb._internal = None                                   # as a fresh process would
    assert rdb.is_excluded("Pulse Walkthrough.txt")
    rdb.set_internal(["Pulse Walkthrough.txt"], False)


def test_marking_internal_rebuilds_the_spec_index():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "Spec Sheet.pdf"), "wb").close()
        before = sales._fingerprint(d, {"Spec Sheet.pdf": ("bv30", "BV30", "Note Validator")})
        rdb.set_internal(["Spec Sheet.pdf"], True)
        after = sales._fingerprint(d, {"Spec Sheet.pdf": ("bv30", "BV30", "Note Validator")})
        rdb.set_internal(["Spec Sheet.pdf"], False)
        assert before != after
        # and its tables are skipped (no PDF is opened for it)
        rdb.set_internal(["Spec Sheet.pdf"], True)
        try:
            assert sales.build_index(d, {"Spec Sheet.pdf": ("bv30", "BV30", "Note Validator")}) == []
        finally:
            rdb.set_internal(["Spec Sheet.pdf"], False)


def _reset(good=None):
    rdb._internal = None
    rdb._internal_good = good


def _corrupt():
    with open(rdb._INTERNAL_PATH, "w", encoding="utf-8") as fh:
        fh.write("{not json")


def _heal():
    for p in (rdb._INTERNAL_PATH, rdb._INTERNAL_PATH + ".corrupt"):
        if os.path.exists(p):
            os.remove(p)
    _reset()


def test_an_unreadable_file_keeps_the_last_good_marks():
    rdb.set_internal(["Secret Note.txt"], True)
    _corrupt()
    rdb._internal = None                    # keep _internal_good
    try:
        with unittest.TestCase().assertLogs("retrieval_db", logging.ERROR):
            rdb.internal_sources()
        assert rdb.is_excluded("Secret Note.txt")
        assert not rdb.is_excluded("Public Manual.txt")
        try:
            rdb.set_internal(["Other.txt"], True)
        except jsonstore.StateUnreadable:
            pass
        else:
            raise AssertionError("set_internal wrote over an unreadable file")
        assert not rdb.is_excluded("Other.txt")
    finally:
        _heal()


def test_an_unreadable_file_on_a_cold_start_holds_everything_back():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "Spec Sheet.pdf"), "wb").close()
        mapping = {"Spec Sheet.pdf": ("bv30", "BV30", "Note Validator")}
        healthy = sales._fingerprint(d, mapping)
        _corrupt()
        _reset(None)
        try:
            with unittest.TestCase().assertLogs("retrieval_db", logging.ERROR):
                rdb.internal_sources()
            assert rdb.is_excluded("Public Manual.txt")
            assert not rdb._matches_scope(
                {"source": "Public Manual.txt", "product": "mycheckr", "prod_mycheckr": True},
                None, None)
            assert sales._fingerprint(d, mapping) != healthy
            assert sales.build_index(d, mapping) == []
        finally:
            _heal()
        assert not rdb.is_excluded("Public Manual.txt")   # absent file = nothing marked


def test_marking_rereads_a_repaired_file():
    rdb.set_internal(["A.txt"], True)
    with open(rdb._INTERNAL_PATH, "w", encoding="utf-8") as fh:   # a hand repair; cache is stale
        json.dump(["A.txt", "B.txt"], fh)
    rdb.set_internal(["C.txt"], True)
    assert all(rdb.is_excluded(n) for n in ("A.txt", "B.txt", "C.txt"))
    rdb.set_internal(["A.txt", "B.txt", "C.txt"], False)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
