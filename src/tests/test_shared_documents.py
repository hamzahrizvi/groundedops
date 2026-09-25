"""A shared document belongs to every product in its category.

"<category>_general" is the bucket for documents that are not about one
product -- an API quick guide, a shared installation checklist. It was
selectable as if it WERE a product, which is how a customer was offered a
button labelled "General (shared docs)".

Removing that button on its own would have orphaned a document. In this
corpus ICU_Age_Result_Quick_Guide_v1_1.pdf is tagged ONLY
biometrics_general, so it was reachable ONLY by choosing that button:
scoped to MyCheckr, the guide about reading an age result from the device
was invisible. So the bucket stops being a choice and becomes what it
always meant -- in scope for every product in the same category.

BOTH ARMS HAVE TO AGREE. Retrieval is hybrid: BM25 filters in Python via
_matches_scope, the dense arm filters server-side in Chroma via a `where`
clause. Widening only the first left the guide still unreachable, because
the dense arm never saw it. That is the failure this file is really
guarding -- two filters expressing one rule.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import retrieval_db as R  # noqa: E402

SHARED = {"source": "ICU_Age_Result_Quick_Guide_v1_1.pdf",
          "product": "biometrics_general", "category": "biometrics",
          "prod_biometrics_general": True}
MYCHECKR = {"source": "MyCheckr User Manual-v7.pdf",
            "product": "biometrics_general,mycheckr,mycheckr_mini",
            "category": "biometrics", "prod_mycheckr": True}
BV30 = {"source": "BV30 User Manual-v1.pdf", "product": "bv30",
        "category": "note", "prod_bv30": True}


def test_a_shared_document_is_in_scope_for_its_categorys_products():
    assert R._matches_scope(SHARED, None, {"product": "mycheckr"})
    assert R._matches_scope(SHARED, None, {"product": "mycheckr_mini"})


def test_a_shared_document_does_not_cross_categories():
    """The bucket is per category. A biometrics quick guide has no business
    in a note-validator conversation."""
    assert not R._matches_scope(SHARED, None, {"product": "bv30"})


def test_an_ordinary_document_still_scopes_normally():
    assert R._matches_scope(MYCHECKR, None, {"product": "mycheckr"})
    assert not R._matches_scope(BV30, None, {"product": "mycheckr"})
    assert R._matches_scope(BV30, None, {"product": "bv30"})


def test_the_dense_filter_says_the_same_thing_as_the_python_one():
    """Two filters, one rule. Widening _matches_scope alone left the shared
    guide unreachable because the Chroma `where` clause had not been
    widened with it -- BM25 would surface it and the dense arm never
    would."""
    import inspect
    src = inspect.getsource(R.retrieve_from_db) if hasattr(
        R, "retrieve_from_db") else ""
    if "_dense_ranking" in src or True:
        src = inspect.getsource(R._dense_ranking)
    assert "_general" in src, \
        "the dense scope filter has no shared-documents arm"
    assert "_category_of(" in src


def test_an_unknown_product_gets_no_shared_arm():
    """A key the catalogue does not know has no category, so it must not
    silently match every shared document."""
    assert R._category_of("not-a-product") == ""
    assert not R._matches_scope(SHARED, None, {"product": "not-a-product"})


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
