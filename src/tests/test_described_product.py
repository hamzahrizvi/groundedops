"""8.17: a product described but not named is recognised, not menued.

"Which age-check device has no screen at all?" retrieved both MyCheckr
manuals near the top and got "which did you mean?", although only the
Mini has no screen. clarify.described_product asks the model, shown each
candidate's own passages; anything but a clean product name keeps the menu.
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clarify  # noqa: E402
import llm  # noqa: E402

NAMES = {"mycheckr": "MyCheckr", "mycheckr_mini": "MyCheckr mini"}
RESULTS = [
    {"product": "mycheckr_mini", "text": "MyCheckr Mini is a more compact "
     "version of the MyCheckr without the presence of a physical screen."},
    {"product": "mycheckr,biometrics_general", "text": "MyCheckr has a touchscreen."},
]
Q = "which age-check device has no screen at all?"


def _described(reply, results=RESULTS):
    seen = []

    def fake(role, prompt, **k):
        seen.append(prompt)
        return {"text": reply, "provider": "deepseek"}
    with mock.patch.object(llm, "generate_with_fallback", fake):
        out = clarify.described_product(Q, ["mycheckr", "mycheckr_mini"],
                                        results, NAMES)
    return out, seen


def test_a_clean_product_name_picks_that_product():
    out, seen = _described("MyCheckr mini")
    assert out == "mycheckr_mini"
    # both candidates' own passages were shown, the comma tag included
    assert "physical screen" in seen[0] and "touchscreen" in seen[0]
    assert _described("**MyCheckr Mini.**")[0] == "mycheckr_mini"


def test_anything_else_keeps_the_menu():
    for reply in ("NONE", "Both of them", "The MyCheckr mini, probably", ""):
        assert _described(reply)[0] is None, reply


def test_no_call_without_passages_for_two_candidates():
    out, seen = _described("MyCheckr mini", results=RESULTS[:1])
    assert out is None and not seen


def test_no_model_keeps_the_menu():
    def down(role, prompt, **k):
        return {"text": "", "provider": "none"}
    with mock.patch.object(llm, "generate_with_fallback", down):
        assert clarify.described_product(Q, list(NAMES), RESULTS, NAMES) is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
