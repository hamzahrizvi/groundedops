"""A document marked internal has no chunks in the index (D-20261024-06):
marking takes them out, ingest keeps the original without indexing it, and
un-marking indexes it again from the original, filed per the catalogue."""
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMP = tempfile.mkdtemp()
os.environ["INTERNAL_SOURCES_PATH"] = os.path.join(_TMP, "internal_sources.json")
os.environ["CHROMA_DIR"] = os.path.join(_TMP, "chroma_db")
os.environ["SOURCE_FILE_DIR"] = os.path.join(_TMP, "documents")

import db  # noqa: E402
import docindex  # noqa: E402
import docstore  # noqa: E402
import ingest  # noqa: E402
import reindex  # noqa: E402
import retrieval_db as rdb  # noqa: E402


class _Vector:
    def tolist(self):
        return [0.1, 0.2]


class _Collection:
    def __init__(self):
        self.rows = {}

    def get(self, where=None, include=None, **kw):
        rows = [(k, v) for k, v in self.rows.items()
                if not where or v[1].get("source") == where.get("source")]
        return {"ids": [k for k, _ in rows], "documents": [v[0] for _, v in rows],
                "metadatas": [dict(v[1]) for _, v in rows]}

    def add(self, ids, documents, embeddings, metadatas):
        for k, t, m in zip(ids, documents, metadatas):
            self.rows[k] = (t, m)

    def delete(self, ids):
        for k in ids:
            self.rows.pop(k, None)

    def count(self):
        return len(self.rows)

    def chunks(self, source):
        return [v for v in self.rows.values() if v[1].get("source") == source]


COL = _Collection()
for p in (patch.object(db, "get_collection", return_value=COL),
          patch.object(ingest, "get_collection", return_value=COL),
          patch.object(ingest, "extract_pages_report",
                       return_value=([(1, "Setting up the device takes four steps.")],
                                     {"pages": 1})),
          patch.object(ingest, "embed_texts", side_effect=lambda t: [_Vector() for _ in t]),
          patch.object(ingest, "_dedupe_enabled", return_value=False),
          patch.object(reindex, "catalog_assignment",
                       return_value={"Pricing.txt": ("cat", "prod_a")})):
    p.start()


def _reset():
    COL.rows.clear()
    docindex.invalidate()
    for n in list(rdb.internal_sources()):
        rdb.set_internal([n], False, reindex=False)


def test_a_marked_upload_is_stored_not_indexed():
    _reset()
    rdb.set_internal(["Pricing.txt"], True)
    report = {}
    assert ingest.ingest_file(b"price list", "Pricing.txt", report=report) == 0
    assert COL.chunks("Pricing.txt") == []
    assert report.get("internal") is True
    assert open(docstore.find("Pricing.txt"), "rb").read() == b"price list"


def test_b_a_marked_document_loses_its_old_chunks_on_ingest():
    _reset()
    assert ingest.ingest_file(b"v1", "Pricing.txt") == 1
    rdb._internal = rdb._internal_good = frozenset(["Pricing.txt"])  # marked behind ingest's back
    assert ingest.ingest_file(b"v2", "Pricing.txt", replace_existing=True) == 0
    assert COL.chunks("Pricing.txt") == []
    assert open(docstore.find("Pricing.txt"), "rb").read() == b"v2"


def test_c_marking_takes_out_only_that_documents_chunks():
    _reset()
    ingest.ingest_file(b"price list", "Pricing.txt")
    ingest.ingest_file(b"manual", "Manual.txt")
    rdb.set_internal(["PRICING.TXT"], True)          # any spelling of the name
    assert COL.chunks("Pricing.txt") == []
    assert len(COL.chunks("Manual.txt")) == 1
    assert docstore.find("Pricing.txt")               # original kept


def test_d_unmarking_indexes_it_again_filed_per_the_catalogue():
    _reset()
    ingest.ingest_file(b"price list", "Pricing.txt")
    rdb.set_internal(["Pricing.txt"], True)
    rdb.set_internal(["Pricing.txt"], False)
    got = COL.chunks("Pricing.txt")
    assert len(got) == 1 and got[0][1]["product"] == "prod_a", got


def test_e_remove_unmarks_without_bringing_it_back():
    _reset()
    ingest.ingest_file(b"price list", "Pricing.txt")
    rdb.set_internal(["Pricing.txt"], True)
    rdb.set_internal(["Pricing.txt"], False, reindex=False)
    assert COL.chunks("Pricing.txt") == []
    rdb.set_internal(["Pricing.txt"], False)          # the sync's own un-mark after Remove
    assert COL.chunks("Pricing.txt") == []


def test_f_no_original_keeps_the_chunks():
    _reset()
    COL.add(["Lost.txt:0"], ["only copy"], [[0.1]], [{"source": "Lost.txt"}])
    rdb.set_internal(["Lost.txt"], True)
    assert len(COL.chunks("Lost.txt")) == 1
    assert rdb.is_excluded("Lost.txt")                # still hidden at read time


def test_g_ocr_and_figure_text_add_nothing_while_marked():
    _reset()
    rdb.set_internal(["Pricing.txt"], True)
    ingest.ingest_file(b"price list", "Pricing.txt")
    assert ingest.ingest_ocr_pages("Pricing.txt", [1, 2]) == 0
    assert ingest.index_figure_text("Pricing.txt") == 0
    assert COL.chunks("Pricing.txt") == []
    assert docstore.ocr_state("Pricing.txt")["done"] == [1, 2]   # approval kept for un-mark


def test_h_an_unreadable_marks_file_stops_ingest_and_keeps_the_chunks():
    _reset()
    assert ingest.ingest_file(b"v1", "Pricing.txt") == 1
    rdb._internal = rdb._Everything()             # cold start, file unreadable
    try:
        try:
            ingest.ingest_file(b"v2", "Pricing.txt", replace_existing=True)
            raise AssertionError("ingest went on with the marks unreadable")
        except RuntimeError as exc:
            assert "unreadable" in str(exc)
        assert len(COL.chunks("Pricing.txt")) == 1
        assert open(docstore.find("Pricing.txt"), "rb").read() == b"v1"
    finally:
        rdb._internal = None


def test_i_an_unreadable_marks_file_stops_a_rebuild_before_the_reset():
    rdb._internal = rdb._Everything()
    try:
        with patch.object(db, "reset_collection") as reset,              patch.object(reindex, "snapshot_collection") as snap:
            try:
                reindex.rebuild([{"source": "Pricing.txt", "path": __file__,
                                  "chunks": 1}], dry_run=False)
                raise AssertionError("rebuild ran with the marks unreadable")
            except RuntimeError as exc:
                assert "unreadable" in str(exc)
        reset.assert_not_called()
        snap.assert_not_called()
    finally:
        rdb._internal = None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
