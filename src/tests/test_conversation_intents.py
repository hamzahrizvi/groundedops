"""The 2026-09-25 conversation fixes, pinned.

Fourteen scripted customer conversations (tests/scenarios/11-24, run live
through tests/run_live.py) found these: a request for a person answered
with "could you tell me more concretely"; a greeting refused with FAQ
suggestions; a warranty question refused instead of routed to sales; the
follow-ups of a comparison asked "which did you mean?" though both
products were named; and "send me the SSP manual as well" not recognised
as a document request because of the trailing "as well".
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import intents                      # noqa: E402
import sales                        # noqa: E402
from doc_request import document_request   # noqa: E402
import _harness                     # noqa: E402,F401
import main                         # noqa: E402


def test_requests_for_a_person_are_recognised():
    for q in ("this is useless, I want to talk to a person",
              "I want to open a support ticket",
              "can someone call me back on 07700 900123",
              "can I speak to a human",
              "escalate this please",
              "how do I contact support",
              "get me through to a real person",
              "is there a person I can talk to",
              "raise a ticket for this"):
        assert intents.is_handoff_request(q), q


def test_technical_questions_are_not_handoffs():
    for q in ("how do I connect it to my machine",
              "how do I connect the NV9USB+ to the host",
              "can I pass the note through twice",
              "what is the ticket capacity of the NV200S",
              "does the ICU support ticketing",
              "how do I call the status endpoint",
              "how do I transfer notes to the cashbox",
              "is there a person detection sensor",
              "what does the operator menu do",
              "how many coins can the hopper pay out"):
        assert not intents.is_handoff_request(q), q


def test_bare_greetings_are_greetings_and_questions_are_not():
    for q in ("hello, how are you today", "Hi there!", "good morning", "hey"):
        assert intents.is_greeting(q), q
    for q in ("hi, how do I reset the BV30", "hello I need the manual",
              "good morning, the NV9 is jammed", "hi"*40):
        assert not intents.is_greeting(q), q


def test_warranty_is_commercial_but_guarantee_the_verb_is_not():
    for q in ("what is the warranty period on the MyCheckr",
              "does it come with a warranty",
              "how long is the guarantee period",
              "is there any guarantee with it"):
        assert sales.is_commercial_question(q), q
    for q in ("how often must I lubricate to guarantee the best performance",
              "is the acceptance rate guaranteed"):
        assert not sales.is_commercial_question(q), q


def test_comparison_follow_up_shapes():
    for q in ("which validates notes faster, the NV9 Spectral or the NV9USB+",
              "do the NV9 Spectral and NV9USB+ both use the same SSP interface",
              "which one should I pick for an arcade cabinet, NV9 Spectral or NV9USB+",
              "does the NV9USB+ use the same bezel options as the NV9 Spectral"):
        assert main._is_comparison(q), q
    # Still narrow: no comparative work, no comparison.
    for q in ("what bezel options are there for the NV9 Spectral",
              "how do I clean the NV9USB+"):
        assert not main._is_comparison(q), q


def test_document_requests_with_trailing_words_and_checklists():
    assert document_request("send me the NV200 Spectral SSP manual as well") == {"kind": "manual"}
    assert document_request("can I download the MyConnect pre-requisites checklist") == {"kind": "guide"}
    assert document_request("give me the quick start guide too") == {"kind": "guide"}
    # Content questions stay content questions.
    assert document_request("what does the manual say about the checklist") is None
