"""The model-name word list leaves out internal documents (D-20261102-02)."""
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
        return 3


CHUNKS = [
    {"text": "The NV9ST board fits the NV9USB+.", "source": "NV9USB Manual.pdf", "product": "nv9usb"},
    {"text": "Service only: the ZZ123 board.", "source": "ZZ Service Note.pdf", "product": "bv30"},
    {"text": "Gateway ZZ456 port map.", "source": "icu_network_api spec.pdf", "product": "myconnect"},
]
for _p in (patch.object(db, "get_collection", return_value=_Col()),
           patch.object(rdb, "_get_bm25_index", return_value=(None, CHUNKS)),
           patch.object(rdb, "_drop_marked_chunks", lambda keys: None)):
    _p.start()

PRE = {"nv", "zz"}


def docs(q):
    return clarify.documented_model_products(q, set(), clarify.product_vocabulary(), PRE)


def unknown(q):
    return clarify.unknown_model_name(q, set(), clarify.product_vocabulary(), PRE)


def test_a_code_only_an_internal_document_prints_is_unknown():
    assert docs("the zz123") == ["bv30"]
    assert unknown("the zz123") is None
    rdb.set_internal(["ZZ Service Note.pdf"], True, copies=False)
    assert docs("the zz123") == []
    assert unknown("the zz123") == "ZZ123"
    assert docs("the nv9st") == ["nv9usb"]
    rdb.set_internal(["ZZ Service Note.pdf"], False, reindex=False)
    assert docs("the zz123") == ["bv30"]
    assert unknown("the zz123") is None


def test_an_excluded_sources_document_is_left_out_too():
    assert unknown("the zz456") == "ZZ456"
    assert docs("the zz456") == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
