"""8.6 -- a comparison reads both manuals.

Two faults, both deterministic:
  * sales._shared_differences grouped rows by the exact "table › attribute"
    string, so two manuals that title the same table in different case
    shared no rows and the comparison fell through (0 shared, 7 once the
    key is case-folded on the live index, 2026-09-29).
  * in a product chat the picker's product won whenever the question named
    two, so retrieval searched one of the two manuals being compared.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sales  # noqa: E402


def _row(product, table, attribute, value, source="m.pdf", page=1):
    return {"product": product, "table": table, "attribute": attribute,
            "values": [value], "source": source, "page": page}


def test_table_titles_that_differ_in_case_are_the_same_row():
    index = [
        _row("a", "Technical Specifications", "Weight", "1.05 kg"),
        _row("b", "TECHNICAL  SPECIFICATIONS", "weight", "0.9 kg"),
        _row("a", "Technical Specifications", "Width", "82 mm"),
        _row("b", "TECHNICAL SPECIFICATIONS", "Width", "82 mm"),
        _row("a", "Technical Specifications", "Only in A", "yes"),
    ]
    diffs = sales._shared_differences(["a", "b"], index, "what is the difference")
    # Open-ended: the rows they disagree on. Width agrees, "Only in A" is
    # not documented for b, so exactly the weight row -- under the first
    # spelling seen.
    assert [(attr, sorted(per.items())) for attr, per in diffs] == [
        ("Technical Specifications › Weight", [("a", "1.05 kg"), ("b", "0.9 kg")])]
    # A named dimension answers that dimension even where they agree.
    focused = sales._shared_differences(["a", "b"], index, "difference in width")
    assert [attr for attr, _ in focused] == ["Technical Specifications › Width"]


def test_compare_builds_a_table_from_case_mismatched_manuals():
    index = [
        _row("a", "Technical Specifications", "Weight", "1.05 kg"),
        _row("b", "TECHNICAL SPECIFICATIONS", "Weight", "0.9 kg"),
    ]
    out = sales.compare("what is the difference between A and B", index,
                        ["a", "b"], {"a": "Product A", "b": "Product B"})
    assert out and out["kind"] == "comparison_specs"
    assert "| Technical Specifications › Weight | 1.05 kg | 0.9 kg |" in out["answer"]


def test_two_named_products_widen_a_product_chat_to_both_manuals():
    # Patched by hand: this file imports main, so the suite runs it in its
    # own process without pytest fixtures.
    import main
    import retrieval_db
    cats = {"nv9usb": "note_val", "nv9_spectral": "note_val", "sku_scs": "coin"}
    real = retrieval_db._category_of
    retrieval_db._category_of = lambda k: cats.get(k, "")
    try:
        # Same category: the category holds both manuals and nothing else.
        assert main._scope_for_products(["nv9usb", "nv9_spectral"]) == {"category": "note_val"}
        # Different categories: only the whole corpus holds both.
        assert main._scope_for_products(["sku_scs", "nv200s"]) is None
    finally:
        retrieval_db._category_of = real
