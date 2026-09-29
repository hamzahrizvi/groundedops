"""8.7: "and step 3?" is served from the previous answer, or the bot says
honestly that the previous answer had no step 3.

Before: retrieval ran on the literal words and re-served the whole
procedure (docs/stress-test-2026-09-25.md:70).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from text_utils import is_more_request, is_step_reference  # noqa: E402


def test_step_references_a_visitor_types():
    for q, n in (("and step 3?", 3), ("step 3", 3), ("Step three", 3),
                 ("what was step 2 again?", 2), ("the third one", 3),
                 ("the 2nd step", 2), ("ok, step #4", 4),
                 ("give me step 1 from that", 1), ("and step 10?", 10)):
        assert is_step_reference(q) == n, q


def test_a_question_that_mentions_a_step_is_not_captured():
    for q in ("from step 3 onwards, is the loom the same",
              "what does step 3 of the NV9 install need",
              "how many steps are there", "what's the next step",
              "step 3 fails with an error"):
        assert is_step_reference(q) is None, q


def test_anything_else_i_should_know_expands():
    assert is_more_request("anything else I should know?")
    assert is_more_request("anything else we need to know")
    assert not is_more_request(
        "Is there anything else I have checked the above and they are alright")


def test_items_are_split_by_their_own_form():
    numbered = ("To reset it:\n\n1. Power off.\n2. Hold the button.\n"
                "   Keep holding for 5 s.\n3. Power on.\n\nThat is all.")
    assert main._answer_steps(numbered) == {
        1: "1. Power off.", 2: "2. Hold the button.\n   Keep holding for 5 s.",
        3: "3. Power on."}
    paren = "1) Open a terminal.\n2) Run `ip addr show`."
    assert main._answer_steps(paren)[2] == "2) Run `ip addr show`."
    # A Step heading's own 1. 2. sub-list is not the procedure.
    headed = ("**Step 1: Create the rule**\n1. Open a terminal.\n"
              "2. Run sudo touch x\n\n**Step 2: Reboot**\nsudo reboot")
    items = main._answer_steps(headed)
    assert sorted(items) == [1, 2]
    assert items[2] == "**Step 2: Reboot**\nsudo reboot"
    hashed = "### Remove the cover\nUnscrew it.\n### Clean the lens\nWipe it."
    assert main._answer_steps(hashed)[2] == "### Clean the lens\nWipe it."
    assert main._answer_steps("The NV9 weighs 1.2 kg.") == {}
    # A code fence after the last step is still the step.
    fenced = "1. Open it.\n2. Run:\n\n```\nsudo reboot\n```"
    assert "sudo reboot" in main._answer_steps(fenced)[2]


def _ask(q, sid="s-87"):
    from unittest import mock
    with mock.patch.object(main, "capability", lambda name: True),             mock.patch.object(main, "log_interaction", lambda *a, **k: None),             mock.patch.object(main, "add_to_memory", lambda *a, **k: None):
        return main.query(main.QueryRequest(q=q, session_id=sid))


def test_step_n_is_served_verbatim_with_the_same_sources():
    src = [{"source": "Linux guide", "page": 3}]
    main._remember_answer("s-87", "1. Open a terminal.\n2. Run a.\n3. Run b.", src)
    r = _ask("and step 3?")
    assert (r["answer"], r["sources"], r["role"]) == ("3. Run b.", src, "step")
    # the record survives, so the next step reference still works
    assert _ask("what about step 1?")["answer"] == "1. Open a terminal."


def test_out_of_range_says_how_many_steps_there_were():
    main._remember_answer("s-87", "1. Open a terminal.\n2. Run a.", [])
    r = _ask("and step 3?")
    assert "had 2 steps" in r["answer"] and r["sources"] == []
    assert r["suggested_replies"] == ["Tell me more"]
    main._remember_answer("s-87", "The NV9 weighs 1.2 kg.", [])
    assert "not a numbered procedure" in _ask("step 2")["answer"]


def test_nothing_is_served_from_a_turn_that_did_not_answer():
    """Every turn pops the record; only answered exits write it back."""
    import inspect
    src = inspect.getsource(main.query)
    assert src.index("_LAST_ANSWER.pop(session_id") < src.index("is_step_reference(")
    main._remember_answer(main.DEFAULT_SESSION_ID, "1. a\n2. b", [])
    assert main.DEFAULT_SESSION_ID not in main._LAST_ANSWER


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
