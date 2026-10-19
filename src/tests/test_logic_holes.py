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


def test_a_refusal_is_remembered_as_its_question_only():
    """8.5: the refused QUESTION stays in memory so the next "which one?"
    can be resolved against it; the refusal text does not, so "tell me
    more" cannot expand a refusal into passages that contradict it."""
    import memory
    sid = "logic-holes-refusal"
    memory.add_to_memory(sid, "how do I change the bezel colour?",
                         "I don't have that in the product documentation.\n\n"
                         "Here are some things I can answer:\n- x")
    assert memory.get_history(sid) == [
        {"q": "how do I change the bezel colour?", "a": "", "refused": True}]
    memory.add_to_memory(sid, "what is the weight?", "The NV9S weighs 1.05 kg.")
    assert len(memory.get_history(sid)) == 2
    assert memory.get_history(sid)[-1]["a"] == "The NV9S weighs 1.05 kg."


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
    faq_store.list_for_product = lambda scope, display_only=False, language=None: [entry]
    faq_store._semantic_scores = lambda q, pool: {"x": 0.0}
    try:
        out = faq_store.suggest_candidates("What is the weight of the NV9S? (NV9 Spectral)", "nv9_spectral")
        assert out["mode"] == "answer", out
    finally:
        faq_store.list_for_product, faq_store._semantic_scores = real_list, real_sem


def test_a_product_form_matches_whole_words_only():
    """"nv9s" is the NV9 Spectral's alias and also the start of "NV9ST";
    "nv9 usb" and "NV9USB+" are both the NV9USB+."""
    real_names, real_aliases = main._product_names, main._product_aliases
    main._product_names = lambda: {"nv9_spectral": "NV9 Spectral", "nv9usb": "NV9USB+",
                                   "sku_scs": "SMART Coin System"}
    main._product_aliases = lambda: {"nv9_spectral": ["NV9S", "NV22"], "nv9usb": ["NV9 USB", "NV9USB"],
                                     "sku_scs": ["SCS"]}
    keys = ["nv9_spectral", "nv9usb", "sku_scs"]
    try:
        assert main._products_named_in("What voltage is supported on the NV9ST?", keys) == []
        assert main._products_named_in("how much does the NV9S weigh", keys) == ["nv9_spectral"]
        assert main._products_named_in("what is the weight of the nv9 usb", keys) == ["nv9usb"]
        assert main._products_named_in("is the NV9USB+ 12V?", keys) == ["nv9usb"]
        assert main._products_named_in("compare NV9S and NV9USB+", keys) == ["nv9_spectral", "nv9usb"]
        assert main._products_named_in("how fast is the smart coin system", keys) == ["sku_scs"]
        assert main._products_named_in("the scs", keys) == ["sku_scs"]
        assert main._products_named_in("discs are not products", keys) == []
    finally:
        main._product_names, main._product_aliases = real_names, real_aliases


def test_a_sibling_is_named_by_its_difference_once_the_family_is_typed():
    """"the mycheckr ... vs the mini" names both; "mini" alone names nothing."""
    real_names, real_aliases = main._product_names, main._product_aliases
    main._product_names = lambda: {"mycheckr": "MyCheckr", "mycheckr_mini": "MyCheckr mini",
                                   "nv9usb": "NV9USB+"}
    main._product_aliases = lambda: {"mycheckr": [], "mycheckr_mini": ["MyCheckr Mini"],
                                     "nv9usb": []}
    keys = ["mycheckr", "mycheckr_mini", "nv9usb"]
    try:
        assert main._products_named_in(
            "how do you fix the mycheckr to a wall vs the mini?", keys) == ["mycheckr", "mycheckr_mini"]
        assert main._products_named_in("does the mycheckr mini need wifi", keys) == ["mycheckr_mini"]
        assert main._products_named_in("is the mycheckr minimal", keys) == ["mycheckr"]
        assert main._products_named_in("nv9usb with a mini usb cable", keys) == ["nv9usb"]
    finally:
        main._product_names, main._product_aliases = real_names, real_aliases


def test_comparison_share_keeps_a_low_scored_table_and_no_third_product():
    """D-20261016-08: C20261017b-34 lost the NV9 Spectral's +5C table (0.0076,
    its side's best 0.045) to the per-product floor; C20261017b-26 filled a
    slot with a third product's MCBF page."""
    k, m = main.CONTEXT_K, main.CONTEXT_MIN
    main.CONTEXT_K, main.CONTEXT_MIN = 8, 4
    try:
        ranked = [{"product": "nv9_spectral", "rerank_score": s} for s in (0.045, 0.024, 0.0076, 0.006)]
        ranked += [{"product": "bv30", "rerank_score": 0.0055}]
        ranked += [{"product": "nv9usb", "rerank_score": s} for s in (0.0043, 0.0031, 0.0023)]
        assert main._comparison_picks(ranked, ["nv9_spectral", "nv9usb"], 0.0225) == [0, 1, 2, 3, 5, 6, 7]
        ranked = [{"product": "nv200s", "rerank_score": 0.88},
                  {"product": "nv9_spectral", "rerank_score": 0.82},
                  {"product": "nv4000", "rerank_score": 0.75},
                  {"product": "nv4000", "rerank_score": 0.67},
                  {"product": "nv4000", "rerank_score": 0.55},
                  {"product": "", "rerank_score": 0.50},
                  {"product": "biometrics_general,mycheckr,mycheckr_mini", "rerank_score": 0.1}]
        assert main._comparison_picks(ranked, ["nv200s", "nv4000"], 0.44) == [0, 2, 3, 4, 5]
        assert main._comparison_picks(ranked, ["mycheckr", "mycheckr_mini"], 0.44) == [5, 6]
    finally:
        main.CONTEXT_K, main.CONTEXT_MIN = k, m


def test_number_rescue_does_not_judge_a_comparison_verdict_or_absence():
    ctx = [{"text": "MCBF: 200,000 Cycles"}, {"text": "MCBI: 25,000 MCBF: 100,000"}]
    verdict = "The NV4000 is better: 100,000 cycles against 200,000 for the NV200 Spectral."
    absent = "The NV200 Spectral MCBF is 200,000 cycles. I could not find an MCBF for the NV4000."
    plain = "The NV200 Spectral MCBF is 200,000 cycles and the NV4000 MCBF is 100,000 cycles."
    assert main._lexically_supported(verdict, ctx)
    assert not main._lexically_supported(verdict, ctx, comparing=True)
    assert not main._lexically_supported(absent, ctx, comparing=True)
    assert main._lexically_supported(plain, ctx, comparing=True)


def test_comparison_rules_reach_the_answer_and_verifier_prompts():
    p = main.build_answer_prompt("", "ctx", "q")
    assert "compare the numbers," in p and "worked out from another product's value" in p
    assert "agrees with the values the SOURCE gives each product" in main._VERIFY_PROMPT
    assert "do not open with the winner" in p


def test_verifier_thinks_on_a_comparison_verdict_or_absence_only():
    q = "which is better for low temp, the nv9usb+ or the nv9 spectral?"
    assert main._needs_judgement(q, "The NV9 Spectral is better: +5C against +3C.")
    assert main._needs_judgement("nv4000 vs nv200 spectral MCBF?", "I could not find an MCBF for the NV4000.")
    assert not main._needs_judgement(q, "NV9USB+: +3C. NV9 Spectral: +5C.")
    assert not main._needs_judgement("what is the nv9 minimum temperature?", "It is lower than 5C: +3C.")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
