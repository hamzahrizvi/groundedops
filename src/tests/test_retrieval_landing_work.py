"""M14: tests for the landing retrieval work that had none before it is
baked into the live index — the phrase arm (_phrase_ranking, and the
RETRIEVAL_PHRASE_GUARANTEE guard in retrieve_from_db) and complete_tables.
carry_table_context and spell_out are already covered in test_tables.py;
these are the two retrieval_db entry points that were missing.

Uses unittest.mock.patch, not the pytest monkeypatch fixture: this file
imports retrieval_db, which run_tests.needs_own_process routes to a fresh
interpreter that calls each test_* function directly, with no pytest
fixtures injected.
"""
import os
import re
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import retrieval_db as rdb


def _chunk(id_, text):
    """A chunk as _get_bm25_index now builds it: text plus its precomputed
    '_flat' field, which _phrase_ranking reads instead of relowercasing."""
    return {"id": id_, "text": text,
            "_flat": " ".join(re.findall(r"[a-z0-9]+", text.lower()))}

TABLE_P1 = """[BV30 User Manual-v1 — Bezel Flash Codes]
Refer to the table below for a summary of the flash codes:
Red Flashes | Blue Flashes | Error | Recommend Action
1 | 2 | Note Path Jam | Check the note path
| 3 | Unit Not Initialised | Initialise the unit"""

TABLE_P2 = """[BV30 User Manual-v1]
| 2 | Interface Checksum | Try to reprogram the firmware
| 3 | EEPROM Checksum | Return to the service centre"""


class _FakeCollection:
    def count(self):
        return 1


# ── _phrase_ranking ───────────────────────────────────────────────────────

def test_bm25_index_precomputes_the_flat_lowercased_text():
    """The precompute this step adds: _get_bm25_index builds '_flat' once
    per chunk, so _phrase_ranking no longer re-lowercases every chunk's
    text on each query."""
    class _FakeChromaCollection:
        def count(self):
            return 1

        def get(self, include):
            return {"ids": ["doc_1"], "documents": ["Payout Module Capacity"],
                    "metadatas": [{"source": "BV30.pdf"}]}

    rdb._bm25_cache["count"] = -1
    _, chunks = rdb._get_bm25_index(_FakeChromaCollection())
    assert chunks[0]["_flat"] == "payout module capacity"


def test_phrase_ranking_ranks_a_verbatim_three_word_run_first():
    chunks = [
        _chunk("docA_1", "General overview of payout systems and modules."),
        _chunk("docA_2", "Payout module capacity up to 80 mixed banknotes is supported."),
        _chunk("docA_3", "Unrelated content about firmware updates."),
    ]
    with patch.object(rdb, "_get_bm25_index", lambda collection: (None, chunks)), \
         patch.object(rdb, "_matches_scope", lambda meta, sf, scope: True):
        hits = rdb._phrase_ranking(
            "What's the payout module capacity on the NV200 Spectral?",
            collection=None, limit=5, source_filter=None, scope=None)

    assert hits == ["docA_2"]


def test_phrase_ranking_returns_nothing_without_a_three_word_content_run():
    called = []

    def _spy(collection):
        called.append(1)
        return None, []

    with patch.object(rdb, "_get_bm25_index", _spy):
        assert rdb._phrase_ranking("how do I reset it", None, 5, None, None) == []
    # Short-circuits before ever touching the BM25 index cache.
    assert called == []


def test_phrase_ranking_respects_scope():
    chunks = [
        _chunk("in_scope_1", "Payout module capacity up to 80 mixed banknotes."),
        _chunk("out_of_scope_1", "Payout module capacity up to 80 mixed banknotes."),
    ]
    with patch.object(rdb, "_get_bm25_index", lambda collection: (None, chunks)), \
         patch.object(rdb, "_matches_scope",
                      lambda meta, sf, scope: meta["id"].startswith("in_scope")):
        hits = rdb._phrase_ranking("payout module capacity", None, 5, None,
                                   {"product": "nv200s"})
    assert hits == ["in_scope_1"]


def test_retrieve_from_db_skips_the_phrase_arm_when_guarantee_is_zero():
    calls = []

    def _spy(*a, **k):
        calls.append(1)
        return ["phrase_hit"]

    with patch.object(rdb, "get_collection", lambda: _FakeCollection()), \
         patch.object(rdb, "_bm25_ranking", lambda *a, **k: []), \
         patch.object(rdb, "_dense_ranking", lambda *a, **k: []), \
         patch.object(rdb, "_get_bm25_index", lambda collection: (None, [])), \
         patch.object(rdb, "_phrase_ranking", _spy):

        with patch.object(rdb, "PHRASE_ARM_GUARANTEE", 0):
            rdb.retrieve_from_db("payout module capacity", top_k=5)
        assert calls == []

        with patch.object(rdb, "PHRASE_ARM_GUARANTEE", 2):
            rdb.retrieve_from_db("payout module capacity", top_k=5)
        assert calls == [1]


# ── complete_tables ────────────────────────────────────────────────────────

def test_complete_tables_appends_the_continuation_of_a_partial_table():
    all_chunks = [
        {"id": "bv30_5", "text": TABLE_P1, "source": "BV30.pdf", "page": 31,
         "section": "Bezel Flash Codes"},
        {"id": "bv30_6", "text": TABLE_P2, "source": "BV30.pdf", "page": 32,
         "section": ""},
    ]
    top_chunks = [{"id": "bv30_5", "text": TABLE_P1, "source": "BV30.pdf", "page": 31}]

    with patch.object(rdb, "get_collection", lambda: object()), \
         patch.object(rdb, "_get_bm25_index", lambda collection: (None, all_chunks)):
        out = rdb.complete_tables(top_chunks)

    assert len(out) == 2
    added = out[1]
    assert added["id"] == "bv30_6"
    assert added["fetched_by"] == "table_completion"


def test_complete_tables_is_idempotent_once_the_continuation_is_already_present():
    all_chunks = [
        {"id": "bv30_5", "text": TABLE_P1, "source": "BV30.pdf", "page": 31,
         "section": "Bezel Flash Codes"},
        {"id": "bv30_6", "text": TABLE_P2, "source": "BV30.pdf", "page": 32,
         "section": ""},
    ]
    top_chunks = [
        {"id": "bv30_5", "text": TABLE_P1, "source": "BV30.pdf", "page": 31},
        {"id": "bv30_6", "text": TABLE_P2, "source": "BV30.pdf", "page": 32},
    ]

    with patch.object(rdb, "get_collection", lambda: object()), \
         patch.object(rdb, "_get_bm25_index", lambda collection: (None, all_chunks)):
        out = rdb.complete_tables(top_chunks)

    assert len(out) == 2


def test_complete_tables_never_raises_on_a_broken_chunk_cache():
    def _boom():
        raise RuntimeError("no collection")

    top_chunks = [{"id": "bv30_5", "text": TABLE_P1, "source": "BV30.pdf", "page": 31}]
    with patch.object(rdb, "get_collection", _boom):
        assert rdb.complete_tables(top_chunks) == top_chunks
