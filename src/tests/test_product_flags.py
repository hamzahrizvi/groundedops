"""Retagging really removes the old product flag from the index (8.2).

Chroma's update MERGES metadata: a key left out of the dict is kept, only
a key set to None is deleted. Every retag path popped the old prod_* flag
and then called update(), so the flag stayed -- 440 chunks in the live
index still carried prod_biometrics_general, and the dense arm's
shared-documents clause matched on it.

These run against a real in-memory Chroma, because the bug lived in
Chroma's semantics; a fake collection would have agreed with the code.
"""
# run-in-own-process
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chromadb  # noqa: E402

import db  # noqa: E402
import repair_product_flags  # noqa: E402

_client = chromadb.EphemeralClient()


def _collection(rows):
    col = _client.create_collection("t" + uuid.uuid4().hex,
                                    embedding_function=None)
    col.add(ids=[r[0] for r in rows], metadatas=[r[1] for r in rows],
            embeddings=[[0.1, 0.2, 0.3]] * len(rows),
            documents=["text"] * len(rows))
    return col


class _Using:
    """Point db at a test collection for the duration of a with-block."""

    def __init__(self, col):
        self.col = col

    def __enter__(self):
        self.real = db.get_collection, db.invalidate_retrieval_cache
        db.get_collection = lambda: self.col
        db.invalidate_retrieval_cache = lambda: None
        return self.col

    def __exit__(self, *exc):
        db.get_collection, db.invalidate_retrieval_cache = self.real


def _meta(col, cid):
    return col.get(ids=[cid], include=["metadatas"])["metadatas"][0]


SHARED_ONCE = {"source": "ICU_Network_API-v1.0.50.pdf",
               "products": "biometrics_general",
               "product": "biometrics_general", "category": "biometrics",
               "prod_biometrics_general": True}


def test_popping_a_key_does_not_delete_it_in_chroma():
    """The semantics the old code missed, pinned so a Chroma upgrade that
    changes them is noticed."""
    col = _collection([("a", dict(SHARED_ONCE))])
    m = _meta(col, "a")
    m.pop("prod_biometrics_general")
    col.update(ids=["a"], metadatas=[m])
    assert _meta(col, "a").get("prod_biometrics_general") is True
    col.update(ids=["a"], metadatas=[{"prod_biometrics_general": None}])
    assert "prod_biometrics_general" not in _meta(col, "a")


def test_with_product_tags_removes_the_old_flag():
    """What /admin/reassign_source now writes."""
    col = _collection([("a", dict(SHARED_ONCE))])
    col.update(ids=["a"],
               metadatas=[db.with_product_tags(_meta(col, "a"), ["mycheckr"])])
    m = _meta(col, "a")
    assert "prod_biometrics_general" not in m
    assert m["prod_mycheckr"] is True
    assert m["products"] == m["product"] == "mycheckr"
    assert m["source"] == SHARED_ONCE["source"]


def test_retag_product_removes_the_old_flag_and_old_spelling():
    col = _collection([("a", dict(SHARED_ONCE))])
    with _Using(col):
        assert db.retag_product("biometrics_general", "mycheckr") == 1
    m = _meta(col, "a")
    assert "prod_biometrics_general" not in m
    assert m["prod_mycheckr"] is True
    assert m["product"] == "mycheckr", "the singular spelling kept the old key"


def test_delete_by_product_on_a_shared_chunk_removes_only_that_flag():
    shared = {"source": "Install.pdf", "products": "mycheckr,mycheckr_mini",
              "product": "mycheckr,mycheckr_mini", "category": "biometrics",
              "prod_mycheckr": True, "prod_mycheckr_mini": True}
    col = _collection([("a", shared)])
    with _Using(col):
        assert db.delete_by_product("mycheckr") == 0
    m = _meta(col, "a")
    assert "prod_mycheckr" not in m
    assert m["prod_mycheckr_mini"] is True
    assert m["products"] == "mycheckr_mini"


def test_repair_deletes_stale_flags_and_nothing_else():
    stale = {"source": "NV9 Spectral Range User Manual-v1.pdf",
             "products": "nv9_spectral", "product": "nv9_spectral",
             "category": "note", "page": 19, "prod_nv9_spectral": True,
             "prod_biometrics_general": True}
    clean = dict(SHARED_ONCE)
    col = _collection([("s", dict(stale)), ("c", clean)])
    with _Using(col):
        assert repair_product_flags.main([]) == 0
        assert _meta(col, "s").get("prod_biometrics_general") is True, \
            "the dry run wrote something"
        assert repair_product_flags.main(["--apply"]) == 0
    m = _meta(col, "s")
    assert "prod_biometrics_general" not in m
    expected = {k: v for k, v in stale.items() if k != "prod_biometrics_general"}
    assert m == expected
    assert _meta(col, "c") == clean


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
