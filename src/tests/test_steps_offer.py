"""An offer the assistant makes, it has to be able to honour.

From the widget transcript of 2026-09-22. The capability reply ended
"Would you like me to walk through the steps?" and NOTHING implemented the
answer:

    visitor: Can I connect to MyCheckr using linux?
    bot:     Yes — that is documented. **Accessing my device in Linux
             Environment-v2-20250224_144232 2** covers linux (page 1).
             Would you like me to walk through the steps?
    visitor: yes
    bot:     Yes, the MyCheckr is an all-in-one device solution that
             performs anonymous age estimation...          <- unrelated
    visitor: yes take me through the steps
    bot:     I could not pin down "yes take me through the steps"...

"yes" carries no content, so retrieval scored it against the corpus and
every branch downstream treated the noise as the question.

WHY NO TEST CAUGHT THIS. Every capability test written when that feature
landed called `answerability.classify()` or `capability_target()`
directly, with chunks supplied by the test. Not one went through a
CONVERSATION, so the offer was verified and the thing it promised was
never exercised at all. The suite was green and the feature was a dead
end. These tests are about the turn AFTER the offer.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


# ── the affirmative ──────────────────────────────────────────────────

def test_the_affirmatives_a_visitor_actually_types():
    for q in ("yes", "Yes", "yes please", "yep", "ok", "sure", "go on",
              "go ahead", "take me through the steps",
              "yes take me through the steps", "show me", "steps"):
        assert main._AFFIRMATIVE.match(q), q


def test_a_real_question_is_not_an_affirmative():
    """The guard runs only with an offer outstanding, but a question that
    happens to follow one must still be answered as a question."""
    for q in ("what is the cashbox capacity",
              "how much does the NV200S weigh",
              "no thanks", "not now", "what else can you tell me"):
        assert not main._AFFIRMATIVE.match(q), q


# ── the offer is remembered, and spent once ──────────────────────────

def test_an_offer_is_consumed_by_the_first_yes_only():
    """A second "yes" is a new question, not the same steps again."""
    main._PENDING_STEPS.clear()
    main._remember_steps_offer("s1", "Doc-v1.pdf")
    assert main._PENDING_STEPS.pop("s1", None) == "Doc-v1.pdf"
    assert main._PENDING_STEPS.pop("s1", None) is None


def test_an_offer_belongs_to_one_session():
    """It must not hijack a "yes" in somebody else's conversation."""
    main._PENDING_STEPS.clear()
    main._remember_steps_offer("s1", "Doc-v1.pdf")
    assert main._PENDING_STEPS.get("s2") is None


def test_the_offer_store_is_bounded():
    main._PENDING_STEPS.clear()
    for i in range(main._PENDING_STEPS_MAX + 5):
        main._remember_steps_offer(f"s{i}", "Doc-v1.pdf")
    assert len(main._PENDING_STEPS) <= main._PENDING_STEPS_MAX


def test_a_session_with_no_id_is_not_remembered():
    main._PENDING_STEPS.clear()
    main._remember_steps_offer(None, "Doc-v1.pdf")
    main._remember_steps_offer("s1", "")
    assert main._PENDING_STEPS == {}


# ── the steps themselves ─────────────────────────────────────────────

def test_steps_are_rendered_verbatim():
    """These are shell commands the reader will type. A model paraphrasing
    `sudo touch /etc/udev/rules.d/80-local.rules` is a support call, so no
    model is involved and nothing is summarised."""
    out = main._format_steps([
        {"section": "Accessing my device › Step 1: Create a rule file",
         "text": "[Doc — Step 1] 1. Open a terminal. 2. Run: "
                 "sudo touch /etc/udev/rules.d/80-local.rules"},
        {"section": "Step 2: Reboot", "text": "[Doc] 1. sudo reboot"},
    ])
    assert "sudo touch /etc/udev/rules.d/80-local.rules" in out
    assert "Step 1: Create a rule file" in out
    assert "Step 2: Reboot" in out
    # The bracketed breadcrumb ingest writes is not shown to a customer.
    assert "[Doc" not in out


# ── the wiring, pinned at the call site ──────────────────────────────

def test_the_offer_is_only_made_when_the_steps_exist():
    """Checked BEFORE the sentence is written, not hoped for afterwards --
    which is the whole fault this file exists for."""
    import inspect
    src = inspect.getsource(main.query)
    i = src.index('role = "capability"')
    block = src[max(0, i - 2000):i]
    assert "_has_steps = bool(_steps_answer(" in block
    assert "if _has_steps:" in block
    assert "_remember_steps_offer(session_id," in block


def test_the_customer_never_sees_the_filename():
    """It used to read "**Accessing my device in Linux Environment-v2-
    20250224_144232 2**" -- an internal filename, a version suffix, an
    ingest timestamp and a stray " 2" from a duplicate upload. The cited
    sources are attached to the response separately."""
    import inspect
    src = inspect.getsource(main.query)
    i = src.index('role = "capability"')
    block = src[max(0, i - 2000):i]
    assert '_doc = _where["source"].rsplit' not in block, \
        "the source filename is being put back into the prose"
    assert "**{_doc}**" not in block


def test_yes_is_answered_before_retrieval_runs():
    """It has to be first. "yes" carries no content, so retrieval scores it
    against the corpus and every branch downstream treats that noise as the
    question -- which is exactly what the transcript shows."""
    import inspect
    src = inspect.getsource(main.query)
    pending = src.index("_PENDING_STEPS.pop(session_id")
    retrieval = src.index("retrieve_fused(")
    assert pending < retrieval, \
        "the affirmative must be handled before retrieval"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
