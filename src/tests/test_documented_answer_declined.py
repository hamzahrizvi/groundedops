"""D-20261102-06: a documented question on a named product is answered, not
declined, deflected or asked "which section?"."""
import sys
from contextlib import nullcontext
from unittest.mock import patch

import _harness  # noqa: F401 -- installs the lightweight main.py stubs first
import main
import sales
from test_self_route import _ask

FIRST = "Red 4 flashes means Feeder Calibration Error."
SECOND = "Red 4 flashes on the Smart Hopper 3 means Coin Jam."
WHY = ("SUPPORT: NO\nRELEVANCE: YES\nThe Smart Hopper 3 source lists red 4 as "
       "Coin Jam, not Feeder Calibration Error.")


def _verifier(reason_about):
    seen = []

    def verified(answer, *a, **k):
        seen.append(answer)
        if len(seen) == 1:
            main._LAST_VERDICT.set((reason_about(answer), WHY))
            return False
        return True
    return verified


def test_the_retry_reads_why_the_checker_said_no():
    result, calls, trace = _ask([FIRST, SECOND], grounded=False,
                                verified=_verifier(lambda a: a),
                                policy={"grounding_retries": 1})
    assert len(calls) == 2
    assert "Coin Jam, not Feeder" in calls[1] and "Coin Jam, not Feeder" not in calls[0]
    assert FIRST in calls[1]
    assert calls[1].rstrip().endswith("Answer:")
    assert result["answer"] == SECOND


def test_a_reason_about_another_answer_leaves_the_retry_blind():
    result, calls, _ = _ask([FIRST, SECOND], grounded=False,
                            verified=_verifier(lambda a: "something else"),
                            policy={"grounding_retries": 1})
    assert len(calls) == 2 and calls[1] == calls[0]


def test_a_product_chat_in_the_middling_band_is_answered():
    result, calls, _ = _ask(["The SD card must be 32GB or smaller."], band="ambiguous")
    assert result.get("role") != "clarify", result.get("role")
    assert "few different sections" not in result["answer"]
    assert len(calls) == 1


def test_no_product_in_the_middling_band_still_asks():
    result, calls, _ = _ask(["unused"], band="ambiguous", product=None)
    assert result.get("role") == "clarify"
    assert not calls


def test_a_category_in_the_product_slot_in_the_middling_band_still_asks():
    real = main._category_keys
    main._category_keys = lambda: {"validators"}
    try:
        result, calls, _ = _ask(["unused"], band="ambiguous", product="validators")
    finally:
        main._category_keys = real
    assert result.get("role") == "clarify", result.get("role")
    assert not calls


def test_the_real_verifier_keeps_its_reason_for_the_answer_it_judged():
    real_gen, real_on = main.generate_with_fallback, main.llm_verify_enabled
    main.generate_with_fallback = lambda *a, **k: {"text": WHY, "provider": "stub"}
    main.llm_verify_enabled = lambda: True
    try:
        # the harness's llm stub has no judging(); the verifier imports it
        with patch.object(sys.modules["llm"], "judging", nullcontext, create=True):
            assert main._llm_verified(FIRST, [{"text": "Red 4: Coin Jam"}], None, "red 4?") is False
    finally:
        main.generate_with_fallback, main.llm_verify_enabled = real_gen, real_on
    assert main._rejection_for(FIRST) == WHY
    assert main._rejection_for(SECOND) == ""


def test_a_warranty_ask_beside_a_price_ask_is_not_deflected():
    assert not sales.is_price_question("how much does the CBA9 cost, and what warranty does it have")
    assert sales.is_price_question("how much does the CBA9 cost")
    assert sales.is_price_question("how much is the extended warranty on the CBA9")
