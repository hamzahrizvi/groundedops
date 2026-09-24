"""Pins for the logic holes found in the 2026-09-25 pipeline audit.

Each test names the plausible customer input that used to go wrong. They
exercise the deciding function directly, with the inputs the audit used to
confirm each fault against the code.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import sales  # noqa: E402


def test_table_punctuation_is_not_a_grounding_unit():
    """A markdown table answer used to fail NLI on its separator row -- the
    gate takes the minimum over units and "|---|---|" entails nothing."""
    from text_utils import split_units
    answer = ("### MDB interface pinout\n\n| Pin | Signal | Notes |\n"
              "|---|---|---|\n| 2 | GND | ground reference |\n"
              "The connector is on the rear of the unit.")
    units = split_units(answer, min_len=5)
    assert not any(re.fullmatch(r"\|?[\s:|\-]+\|?", u) for u in units)
    assert not any(u.startswith("#") for u in units)
    assert "MDB interface pinout" in units
    assert any("rear of the unit" in u for u in units)


def test_number_rescue_needs_whole_numbers_and_no_table():
    ctx = [{"text": "Capacity: 300 notes. Supply 24V DC. Revised 2012. Weight 1.25 Kg."}]
    assert not main._lexically_supported("It holds 30 notes.", ctx)
    assert not main._lexically_supported("It needs 12V DC.", ctx)
    assert not main._lexically_supported("It weighs 1.2 kg.", ctx)
    assert main._lexically_supported("It holds 300 notes.", ctx)
    assert main._lexically_supported("It weighs 1.25 kg.", ctx)
    assert not main._lexically_supported("| Pin | V |\n|---|---|\n| 1 | 24 |", ctx)


def test_a_yes_is_short_and_carries_no_question():
    yes = ("yes", "Yes", "yes please", "yep", "ok", "sure", "go on", "go ahead",
           "take me through the steps", "yes take me through the steps",
           "show me", "steps")
    for q in yes:
        assert main._is_bare_affirmative(q), q
    no = ("Yesterday the NV9 stopped accepting notes",
          "okay so what about android then?", "please tell me the weight",
          "Step 3 failed, what now?", "sure, but what does the BV30 weigh?",
          "no thanks", "what else can you tell me")
    for q in no:
        assert not main._is_bare_affirmative(q), q


def test_technical_words_are_not_commercial():
    technical = ("why does the API response have quotes around the value?",
                 "how do I subscribe to age result events?",
                 "what is the availability of the RS232 port?",
                 "can I use a third-party power supplier with the NV9?")
    for q in technical:
        assert not sales.is_commercial_question(q), q
    commercial = ("can I get a quote for 50 units", "is the NV200 in stock?",
                  "what is the subscription cost?", "who is your UK supplier?",
                  "what is the lead time on the BV30?",
                  "is it available to buy direct?")
    for q in commercial:
        assert sales.is_commercial_question(q), q


def test_a_refusal_is_not_remembered():
    import memory
    sid = "logic-holes-refusal"
    memory.add_to_memory(sid, "how do I change the bezel colour?",
                         "I don't have that in the product documentation.\n\n"
                         "Here are some things I can answer:\n- x")
    assert memory.get_history(sid) == []
    memory.add_to_memory(sid, "what is the weight?", "The NV9S weighs 1.05 kg.")
    assert len(memory.get_history(sid)) == 1


def test_an_off_topic_deferral_does_not_claim_the_turn():
    """Unless it comes from the best passage retrieval found: "screen size"
    shares no word with "the dimensions of the device" and is still it."""
    import answerability
    real = None
    try:
        import crossrefs
        real = crossrefs.deferral_for
        crossrefs.deferral_for = lambda q, chunks: {
            "title": "Service Guide", "topic": "jam recovery",
            "source": "NV200S.pdf", "page": 84, "on_topic": False, "rank": 3}
        out = answerability.classify("how do I change the bezel colour?", [
            {"text": "For jam recovery see the Service Guide.", "source": "NV200S.pdf",
             "page": 84, "product": "nv200s"}])
        assert out["kind"] != answerability.DOCUMENTED_ELSEWHERE, out
        crossrefs.deferral_for = lambda q, chunks: {
            "title": "Service Guide", "topic": "bezel colour",
            "source": "NV200S.pdf", "page": 84, "on_topic": True, "rank": 3}
        out = answerability.classify("how do I change the bezel colour?", [
            {"text": "For bezel colour see the Service Guide.", "source": "NV200S.pdf",
             "page": 84, "product": "nv200s"}])
        assert out["kind"] == answerability.DOCUMENTED_ELSEWHERE, out
    finally:
        if real is not None:
            crossrefs.deferral_for = real


def test_the_picker_is_overridden_by_the_typed_question_only():
    """The scope override reads the typed text when a picker product is
    set; the condensed rewrite carries the PREVIOUS product and must not."""
    import inspect
    src = inspect.getsource(main.query)
    assert "_named_typed if payload.product else _named" in src
    assert "_named_typed = _products_named_in(_normalize_query(q), _keys)" in src


def test_a_curated_question_typed_in_a_product_chat_is_served():
    """The pipeline appends "(Product Name)" for retrieval; the verbatim
    FAQ comparison must see the question the visitor typed."""
    import faq_store
    entry = {"id": "x", "question": "What is the weight of the NV9S?", "answer": "1.05 kg"}
    real_list, real_sem = faq_store.list_for_product, faq_store._semantic_scores
    faq_store.list_for_product = lambda scope: [entry]
    faq_store._semantic_scores = lambda q, pool: {"x": 0.0}
    try:
        out = faq_store.suggest_candidates("What is the weight of the NV9S? (NV9 Spectral)", "nv9_spectral")
        assert out["mode"] == "answer", out
    finally:
        faq_store.list_for_product, faq_store._semantic_scores = real_list, real_sem


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
