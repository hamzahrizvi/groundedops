"""One answering copy per file (D-20261026-91). The Confluence sync stores a
file once per page that attaches it ("X (att123).pdf"); every copy was
indexed and answered, so one drawing filled the candidate slots. Copies are
found by content hash when the chunk cache is built: the plain name answers,
carrying the filing of its copies, and the copies are left out. A copy of a
marked document is marked too."""
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
import retrieval_db as rdb  # noqa: E402


class _Vector:
    def tolist(self):
        return [0.1, 0.2]


def _where_ok(meta, where):
    """The subset of Chroma's `where` the dense arm sends."""
    if not where:
        return True
    if "$and" in where:
        return all(_where_ok(meta, w) for w in where["$and"])
    if "$or" in where:
        return any(_where_ok(meta, w) for w in where["$or"])
    (k, v), = where.items()
    if isinstance(v, dict) and "$in" in v:
        return meta.get(k) in v["$in"]
    if isinstance(v, dict) and "$nin" in v:
        return meta.get(k) not in v["$nin"]
    return meta.get(k) == v


class _Collection:
    def __init__(self):
        self.rows = {}
        self.wheres = []

    def get(self, where=None, include=None, **kw):
        rows = [(k, v) for k, v in self.rows.items() if _where_ok(v[1], where)]
        return {"ids": [k for k, _ in rows], "documents": [v[0] for _, v in rows],
                "metadatas": [dict(v[1]) for _, v in rows]}

    def query(self, query_embeddings, n_results, where=None, include=None):
        self.wheres.append(where)
        rows = [(k, v) for k, v in self.rows.items() if _where_ok(v[1], where)][:n_results]
        return {"ids": [[k for k, _ in rows]], "metadatas": [[dict(v[1]) for _, v in rows]]}

    def add(self, ids, documents, embeddings, metadatas):
        for k, t, m in zip(ids, documents, metadatas):
            self.rows[k] = (t, m)

    def delete(self, ids):
        for k in ids:
            self.rows.pop(k, None)

    def count(self):
        return len(self.rows)

    def sources(self):
        return {v[1]["source"] for v in self.rows.values()}


COL = _Collection()
for p in (patch.object(db, "get_collection", return_value=COL),
          patch.object(rdb, "get_collection", return_value=COL),
          patch.object(ingest, "get_collection", return_value=COL),
          patch.object(ingest, "extract_pages_report",
                       return_value=([(1, "Fit the bezel with four M3 screws.")],
                                     {"pages": 1})),
          patch.object(ingest, "embed_texts", side_effect=lambda t: [_Vector() for _ in t]),
          patch.object(rdb, "embed_query", return_value=_Vector()),
          patch.object(ingest, "_dedupe_enabled", return_value=False)):
    p.start()

DRAWING = b"WR02040_4 drawing"


def _reset():
    COL.rows.clear()
    for n in list(docstore.load_manifest()["documents"]):
        docstore.delete(n)
    for n in list(rdb.internal_sources()):
        rdb.set_internal([n], False, reindex=False)
    db.invalidate_retrieval_cache()


def _ingest(name, content=DRAWING, product="bv100", category="note"):
    ingest.ingest_file(content, name, product_key=product, category_key=category)
    db.invalidate_retrieval_cache()


def _bm25_sources():
    _, chunks = rdb._get_bm25_index(COL)
    return {c["source"] for c in chunks}


def test_a_the_plain_name_answers_over_its_sync_copies():
    _reset()
    _ingest("WR02040_4 (att123).pdf", product="bv20")
    _ingest("WR02040_4.pdf")
    _ingest("WR02040_4 (456).pdf", product="cashgenic")
    assert _bm25_sources() == {"WR02040_4.pdf"}
    assert rdb.duplicate_of("WR02040_4 (att123).pdf") == "WR02040_4.pdf"
    assert rdb.duplicate_of("WR02040_4.pdf") is None


def test_b_with_no_plain_name_the_first_indexed_answers():
    _reset()
    _ingest("Fit (att2).pdf")
    _ingest("Fit (att1).pdf")
    for _, m in COL.rows.values():             # same second: say which came first
        m["indexed_at"] = "2026-01-0%d" % (1 if m["source"] == "Fit (att2).pdf" else 2)
    db.invalidate_retrieval_cache()
    assert _bm25_sources() == {"Fit (att2).pdf"}


def test_c_a_copy_is_out_of_every_scope_unless_asked_for_by_name():
    _reset()
    _ingest("WR02040_4.pdf")
    _ingest("WR02040_4 (att123).pdf", product="bv20")
    copy = {"source": "WR02040_4 (att123).pdf", "product": "bv20", "prod_bv20": True}
    for scope in (None, {"product": "bv20"}, {"category": "note"}):
        assert not rdb._matches_scope(copy, None, scope)
    assert rdb._matches_scope(copy, copy["source"], None)


def test_d_the_answering_copy_is_filed_wherever_a_copy_is():
    _reset()
    _ingest("WR02040_4.pdf")
    _ingest("WR02040_4 (att123).pdf", product="bv20", category="coin")
    keep = {"source": "WR02040_4.pdf", "product": "bv100", "prod_bv100": True,
            "category": "note"}
    assert rdb._matches_scope(keep, None, {"product": "bv20"})
    assert rdb._matches_scope(keep, None, {"category": "coin"})
    assert not rdb._matches_scope(keep, None, {"product": "nv9"})
    _, chunks = rdb._get_bm25_index(COL)
    assert {c["product"] for c in chunks} == {"bv100,bv20"}
    assert all(rdb._matches_scope(c, None, {"product": "bv20"}) for c in chunks)


def test_e_the_dense_arm_never_returns_a_copy_and_finds_the_carrier():
    _reset()
    _ingest("WR02040_4.pdf")
    _ingest("WR02040_4 (att123).pdf", product="bv20")
    for scope in (None, {"product": "bv20"}, {"category": "note"}):
        ids = rdb._dense_ranking("bezel", COL, 5, None, scope)
        assert ids and all(i.startswith("WR02040_4.pdf:") for i in ids), (scope, ids)
    assert {"source": {"$nin": ["WR02040_4 (att123).pdf"]}} in COL.wheres[-1]["$and"]
    ids = rdb._dense_ranking("bezel", COL, 5, "WR02040_4 (att123).pdf", None)
    assert ids and all(i.startswith("WR02040_4 (att123).pdf:") for i in ids)


def test_f_asking_about_a_copy_reads_the_answering_copy():
    _reset()
    _ingest("WR02040_4.pdf")
    _ingest("WR02040_4 (att123).pdf", product="bv20")
    got = rdb.retrieve_from_db("bezel screws", top_k=4,
                               source_filter="WR02040_4 (att123).pdf")
    assert got and {r["source"] for r in got} == {"WR02040_4.pdf"}


def test_g_removing_the_answering_copy_lets_a_copy_answer():
    _reset()
    _ingest("WR02040_4.pdf")
    _ingest("WR02040_4 (att123).pdf", product="bv20")
    db.delete_source("WR02040_4.pdf")
    db.invalidate_retrieval_cache()
    assert _bm25_sources() == {"WR02040_4 (att123).pdf"}
    assert rdb.duplicate_of("WR02040_4 (att123).pdf") is None


def test_h_a_copy_of_a_marked_document_is_stored_not_indexed_and_marked():
    _reset()
    _ingest("CN00392.pdf", b"change notice")
    rdb.set_internal(["CN00392.pdf"], True)
    report = {}
    assert ingest.ingest_file(b"change notice", "CN00392 (att9).pdf", report=report) == 0
    assert report.get("internal") is True
    assert rdb.is_marked_internal("CN00392 (att9).pdf")
    assert not COL.sources() & {"CN00392.pdf", "CN00392 (att9).pdf"}
    assert docstore.find("CN00392 (att9).pdf")


def test_i_marking_one_copy_marks_every_stored_copy_and_its_chunks_go():
    _reset()
    _ingest("CN00392.pdf", b"change notice")
    _ingest("CN00392 (att9).pdf", b"change notice")
    _ingest("Other.pdf", b"another file")
    rdb.set_internal(["CN00392 (att9).pdf"], True)
    assert rdb.is_marked_internal("CN00392.pdf")
    assert not rdb.is_marked_internal("Other.pdf")
    assert COL.sources() == {"Other.pdf"}


def test_j_marking_a_document_internal_on_ingest_marks_its_public_copies():
    _reset()
    _ingest("Price (att1).pdf", b"prices")
    rdb.set_internal(["Price.pdf"], True, reindex=False)
    assert ingest.ingest_file(b"prices", "Price.pdf") == 0
    assert rdb.is_marked_internal("Price (att1).pdf")
    assert COL.sources() == set()


def test_k_the_documents_page_shows_the_duplicate():
    _reset()
    _ingest("WR02040_4.pdf")
    _ingest("WR02040_4 (att123).pdf", product="bv20")
    import routes_documents
    with patch.object(routes_documents, "_require_admin", return_value=None):
        rows = {r["source"]: r for r in routes_documents.admin_sources(None)["sources"]}
    assert rows["WR02040_4 (att123).pdf"]["duplicate_of"] == "WR02040_4.pdf"
    assert rows["WR02040_4.pdf"]["duplicate_of"] is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
