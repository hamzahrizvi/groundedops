"""A follow-up that ends in a refusal asks a question instead of stopping.

Every clarify branch used to sit inside `if confidence == "none"`, and with
RETRIEVAL_GATE_THRESHOLD at 0.0001 retrieval essentially never reports
"none" -- so the system could only ask a question when retrieval had failed
outright, which is the case where asking helps least.

The case that prompted this (logs.jsonl:1968-1970): "how about RMS" was told
the documentation did not cover it, while the RMS figures sat in the manual
that had been retrieved and cited. Asked as a full question one turn later,
the same corpus answered it.
"""
import _harness  # noqa: F401 -- load main with lightweight test dependencies
import main


def test_clarify_markers_detect_our_own_questions():
    hist = [{"q": "how about RMS",
             "a": "I don't have more detail beyond what we already covered "
                  "for \"x\" — could you tell me more concretely what you'd "
                  "like me to check or expand on?"}]
    assert main._asked_to_clarify_last_turn(hist) is True


def test_a_real_answer_ending_in_a_question_is_not_a_clarify():
    """Matching our templates, not "ends in ?" -- a genuine answer can end in
    a question mark and must not suppress the next clarify."""
    hist = [{"q": "which interface?",
             "a": "The NV9USB+ needs an IF5 for MDB. Did you want the pinout?"}]
    assert main._asked_to_clarify_last_turn(hist) is False


def test_no_history_is_not_a_clarify():
    assert main._asked_to_clarify_last_turn([]) is False
    assert main._asked_to_clarify_last_turn(None) is False


def test_the_gate_is_guarded_on_all_three_conditions():
    """Each guard is load-bearing and each has a failure mode if dropped:
    an outage would have the bot asking visitors to rephrase; a standalone
    miss would loop forever; and two questions in a row reads as an
    assistant that cannot answer anything."""
    import inspect
    src = inspect.getsource(main.query)
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "if offer_support and not system_refusal:" in code, \
        "an outage must never be answered with a clarifying question"
    assert "is_followup_turn(q, history, resolved_query)" in code, \
        "a standalone miss must still get the flat refusal"
    assert "_asked_to_clarify_last_turn(history)" in code, \
        "the assistant must not ask twice in a row"
    assert "offer_support = False" in code, \
        "a clarify is a working conversation, not a dead end"


def test_main_response_carries_the_clarify_fields():
    """They existed only on the early-return branches, so a clarify raised
    on the generation path would have been invisible to every client."""
    import inspect
    src = inspect.getsource(main.query)
    tail = src[src.rindex("return {"):]
    assert '"needs_clarification": needs_clarification' in tail
    assert '"clarification_options": clarification_options' in tail


# The set-anaphora markers are tested in test_reference_markers.py, which
# does NOT import _harness: the harness stubs text_utils, so asserting on
# has_reference_markers from here tests the stub rather than the code.


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
