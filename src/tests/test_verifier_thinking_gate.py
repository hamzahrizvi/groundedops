"""The LLM verifier thinks on flash/LED/colour/code answers and nowhere else.

Measured 2026-09-27 (see main._needs_judgement): thinking changed exactly
one verdict out of six -- it rejected "NV200S red x3 = Unit Not
Initialised" (the manual says red 1 + blue 3), which the fast verifier
accepted 3/3 -- and cost 3-13x the time on every other check.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import llm  # noqa: E402

THINK = [
    # the answer that thinking caught
    ("NV200 Spectral bezel LED flashes red 3 times then pauses (NV200S)",
     "A red flash sequence of 3 on the NV200 Spectral bezel indicates Unit Not Initialised."),
    # a follow-up that names no flash: its answer does
    ("how do I clear that",
     "Unit Not Initialised (1 red, 3 blue flashes) needs the unit returned for repair."),
    ("what do the LED colours on the SMART Coin System mean", "Green means ready."),
    ("my NV9USB+ shows fault code 12", "That is a note path jam."),
    ("the status light keeps blinking", "Check the power supply."),
]
FAST = [
    ("how do I install the SMART Coin System in the machine",
     "Screw the Baseplate into the machine with six screws, then slide the unit on."),
    ("how do I poll the NV200 Spectral over SSP (NV200S)",
     "Send command 0x07 with no data; the reply lists events since the last poll."),
    ("what bezel options are there for the NV9 Spectral", "Flat and tall bezels are available."),
    ("how do I tell the BV30 which notes to accept", "Use the inhibit inputs on the interface."),
    ("is there example code in C# for the key exchange", "The SSP manual has a C# example."),
]


def test_flash_led_colour_and_code_answers_think():
    for q, a in THINK:
        assert main._needs_judgement(q, a), (q, a)


def test_install_spec_and_protocol_answers_stay_fast():
    for q, a in FAST:
        assert not main._needs_judgement(q, a), (q, a)


def test_verifier_thinking_always_restores_1f594c0():
    old = os.environ.get("VERIFIER_THINKING")
    os.environ["VERIFIER_THINKING"] = "always"
    try:
        assert main._needs_judgement(*FAST[0])
    finally:
        if old is None:
            os.environ.pop("VERIFIER_THINKING", None)
        else:
            os.environ["VERIFIER_THINKING"] = old


def _judging_seen(q, a):
    """Run the real _llm_verified with the model call stubbed; report
    whether the call was made inside llm.judging()."""
    seen = []
    real_gen, real_on = main.generate_with_fallback, main.llm_verify_enabled

    def fake(role, prompt, **kw):
        seen.append(llm._judging.get())
        return {"text": "SUPPORT: YES\nRELEVANCE: YES\nok", "provider": "stub"}

    main.generate_with_fallback = fake
    main.llm_verify_enabled = lambda: True
    try:
        ok = main._llm_verified(a, [{"text": "source text"}], None, q)
    finally:
        main.generate_with_fallback, main.llm_verify_enabled = real_gen, real_on
    assert ok is True and len(seen) == 1
    return seen[0]


def test_llm_verified_sends_thinking_only_when_needed():
    assert _judging_seen(*THINK[0]) is True
    assert _judging_seen(*FAST[0]) is False
    assert _judging_seen(*VALUE_VERDICT[0]) is True


VALUE_VERDICT = [
    ("got a cba9 that's 2 years old, is it still covered",
     "Yes, it is still covered. The CBA9 has a standard warranty of 1 year or 200,000 cycles."),
    ("which hopper takes a 26mm coin that's 2.5mm thick",
     "The Single Hoppers do not: Position 3 is 24-33 mm, limited to 1-3 mm thickness."),
    ("can the nv200 spectral run off our 12v rail", "No, it needs 24 V DC."),
]


def test_verdict_on_customers_own_figure_thinks():
    for q, a in VALUE_VERDICT:
        assert main._needs_judgement(q, a), (q, a)


def test_product_digits_are_not_a_customers_figure():
    # "CBA9" and "NV9USB+" name products; no value given, so fast as before
    assert not main._needs_judgement("is the CBA9 covered by warranty",
                                     "Yes, it has a 1 year warranty.")
    assert not main._needs_judgement("does the NV9USB+ take euros",
                                     "Yes, it accepts 5 to 500 euro notes.")


def test_number_match_does_not_pass_a_verdict_on_customers_figure():
    q, a = VALUE_VERDICT[0]
    chunks = [{"text": "Standard warranty: 1 year or 200,000 cycles. 2 tiers."}]
    # every number is in the passage: passes without the question, as before
    assert main._lexically_supported(a, chunks)
    # with the question giving the customer's figure, the model checker decides
    assert not main._lexically_supported(a, chunks, question=q)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
