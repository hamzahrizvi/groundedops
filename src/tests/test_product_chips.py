"""Which did you mean? — offered by product NAME, never by database tag.

From the console transcript of 2026-09-22:

    That could apply to more than one product - which did you mean?
      [ myconnect,biometrics_general ]      <- a raw database tag
      [ NV200S ]

Two faults in one line.

  * The span was read from the `product` metadata field RAW, and that
    field is a comma-joined list when a document belongs to more than one
    product. "myconnect,biometrics_general" is not a product, has no
    display name, and fell through to being shown verbatim.
  * Clicking it sent that whole string back as the scope, which matches no
    product, so the next turn asked the same question again. The transcript
    shows it doing that three times in a row -- the button could not work.

And "<category>_general" is not something a customer can mean either. It
is the bucket for documents that are not about one product, so it is no
longer offered; those documents are in scope for every product in their
category instead (test_shared_documents.py).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def test_a_multi_product_tag_becomes_one_chip_per_product():
    """THE BUG. One document tagged to two products must offer two
    buttons, not one button labelled with the join."""
    keys = main._product_keys_in([{"product": "mycheckr,mycheckr_mini"}], 8)
    assert keys == ["mycheckr", "mycheckr_mini"]


def test_the_shared_bucket_is_never_offered():
    keys = main._product_keys_in(
        [{"product": "myconnect,biometrics_general"},
         {"product": "biometrics_general"}], 8)
    assert keys == ["myconnect"]


def test_no_chip_is_ever_a_raw_tag():
    """The label lookup falls back to the key when it does not recognise
    it, which is how the join reached a customer. Every key produced here
    must resolve to a real name."""
    results = [{"product": "myconnect,biometrics_general"},
               {"product": "nv200s"},
               {"product": "mycheckr,mycheckr_mini"}]
    names = main._product_names()
    for key in main._product_keys_in(results, 8):
        assert "," not in key, key
        assert not key.endswith("_general"), key
        assert names.get(key), f"{key} has no display name"


def test_an_untagged_result_contributes_nothing():
    assert main._product_keys_in(
        [{"product": ""}, {}, {"product": "   "}], 8) == []


def test_only_the_context_window_is_considered():
    """A product that only appears below the cut is not something the
    answer would have used, so offering it would widen the question."""
    results = [{"product": "mycheckr"}] * 3 + [{"product": "bv30"}]
    assert main._product_keys_in(results, 3) == ["mycheckr"]


def test_the_span_is_built_from_keys_at_the_call_site():
    """Pins the wiring: reading the tag field raw is what produced the
    join, so the raw read must not come back."""
    import inspect
    src = inspect.getsource(main.query)
    assert "_product_keys_in(results, CONTEXT_K)" in src
    assert 'r.get("product") or "") for r in results' not in src, \
        "the product span is being read from the raw tag field again"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
