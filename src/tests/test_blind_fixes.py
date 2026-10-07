"""S30: fixes found by blind sets 4, 5 and NV4000 (2026-10-07).

Imports main, so the suite runs it in its own process; no pytest fixtures.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def test_a_yes_that_explains_a_no_becomes_no():
    """N4-15: 3.3V against a +3.7V logic-high minimum."""
    a = ("Yes. The NV4000 defines Logic High on its inputs as +3.7V to +12V, "
         "so a 3.3V drive does not meet the documented Logic High minimum.")
    assert main._agree_verdict(a).startswith("No. The NV4000")
    assert main._strip_preamble(a).startswith("No.")
    assert main._agree_verdict(
        "Yes. Logic High is +3.7V to +12V, so 3.3V falls below the documented "
        "logic-high range and would not be read as high.").startswith("No.")
    # A conditional limit, or a real Yes, is left alone.
    for keep in ("Yes, as long as the supply does not exceed the maximum of 26.4V.",
                 "Yes. It accepts 12V and 24V supplies.",
                 "Yes, if the input is not below the minimum of 3.7V.",
                 "No. 3.3V does not meet the minimum."):
        assert main._agree_verdict(keep) == keep, keep


def test_a_passage_is_labelled_with_its_documents_product():
    """N4-29/N4-30: the NV4000 manual calls its head "NV200 Spectral"."""
    names = {"nv4000": "NV4000", "nv200s": "NV200 Spectral"}
    assert main._passage_product({"product": "nv4000"}, names) == \
        ", from the NV4000 documentation"
    # Shared or multi-product documents, and unknown keys, carry no label.
    for chunk in ({"product": "nv4000,nv200s"}, {"product": "note_val_general"},
                  {"product": "gone"}, {}):
        assert main._passage_product(chunk, names) == "", chunk


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
