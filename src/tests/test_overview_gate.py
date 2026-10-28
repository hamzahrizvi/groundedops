"""D-20261014-06: an unscoped question about a model we do not make, or about
something no product does, no longer gets the "Here is what we make that
fits" overview.

"whats" matched every overview on the stop word "what"; "a good pub near
your office" matched MyCheckr on "good"; "the NV99 Titan" matched on
"validator"/"casino". The overview now needs real stems, is withheld when
the model labels the question off-topic, and an unknown model is said to be
unknown before any catalogue answer.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _harness  # noqa: F401,E402 -- load main with lightweight test dependencies
import main  # noqa: E402
import sales  # noqa: E402
import clarify  # noqa: E402

OVERVIEW = {"answer": "Here is what we make that fits: ...", "kind": "overview_match"}


def _overview(name, answer):
    return {"origin": "harvested", "question": f"What is the {name} and what does it do?",
            "answer": answer}


def test_whats_no_longer_matches_every_overview():
    items = [_overview("NV9 Spectral", "A banknote validator with casino level security."),
             _overview("MyCheckr Mini", "Estimates age at the counter.")]
    out = sales.answer("whats good for a casino floor, do you have anything?", items, [], None)
    assert out["kind"] == "overview_match"
    assert "casino" in out["answer"] and "Estimates age" not in out["answer"]


def _sales(q, verdict, unknown=None, scope=None, scoped=False):
    with patch.object(sales, "get_index", return_value=[]), \
         patch.object(sales, "answer", return_value=dict(OVERVIEW)) as ans, \
         patch.object(main, "_source_to_product", return_value={}), \
         patch.object(main, "_product_names", return_value={}), \
         patch.object(main, "_products_named_in", return_value=[]), \
         patch.object(main, "_is_product_scope", return_value=scoped), \
         patch.object(main, "_unknown_product_text", return_value=unknown) as unk, \
         patch.object(main, "_friendly_refusal",
                      return_value="I don't have that in the product documentation."), \
         patch.object(clarify, "classify_unscoped", return_value=verdict), \
         patch.object(main.faq_store, "_load", return_value=[]):
        return main._sales_answer(q, None, scope), ans, unk


def test_unscoped_off_topic_overview_is_declined():
    q = "can you recommend a good pub near your office"
    assert _sales(q, "other")[0]["kind"] == "declined"
    assert _sales(q, "product")[0]["kind"] == "overview_match"
    assert _sales(q, None)[0]["kind"] == "overview_match"


def test_unknown_model_answers_before_the_catalogue():
    text = "I have no documentation for a product called NV99. Did you mean the NV9 Spectral?"
    out, ans, _ = _sales("which of your validators is the NV99 Titan", "product", unknown=text)
    assert out["kind"] == "unknown_model" and out["answer"] == text
    assert not ans.called
    _, _, unk = _sales("which of your validators is the NV99 Titan", "product",
                       unknown=text, scoped=True)
    assert not unk.called


def test_route_roles():
    from test_pipeline_trace import _ask, _log_spy
    for kind, role in (("declined", "rejected"), ("unknown_model", "clarify")):
        rows, spy = _log_spy()
        with patch.object(main, "_sales_answer",
                          return_value={"answer": "x", "kind": kind}):
            out = _ask(log=spy)
        assert out["role"] == role, (kind, out["role"])
        assert out["offer_support"] is (role == "rejected")
        assert rows[0]["role"] == role


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
