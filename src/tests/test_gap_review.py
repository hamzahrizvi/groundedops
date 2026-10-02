"""10.2: a gap entry knows who asked it and how the turn then ended.

record_gap stamps M2's origin and leaves its id on the trace; after query()
returns, main._stamp_gap writes the outcome (answered/refused, role,
grounding, pages, the answer) onto that entry. /faq/gaps hides entries only
the harness asked, unless include_tests is set.
"""
import _harness
from fastapi.testclient import TestClient

import faq_store
import main
import pipeline_trace

client = TestClient(_harness.app)
ADMIN = {"x-admin-password": _harness.SUPPORT_TOKEN}


def _ask(q, origin, result):
    tok = pipeline_trace.start()
    try:
        pipeline_trace.set_meta(origin=origin)
        faq_store.record_gap(q, "p_review")
        pipeline_trace.mark("respond", "test")
        main._stamp_gap(result)
    finally:
        pipeline_trace.reset(tok)


def _gap(q):
    return next(g for g in faq_store.list_gaps(None) if g["question"] == q)


def test_answered_turn_is_stamped_with_what_was_said():
    q = "what is the bezel colour of the review unit"
    _ask(q, "widget", {"role": "fast", "answer": "It is black.", "grounding_score": 0.9,
                       "sources": [{"source": "Review.pdf", "pages": [3, 4]}]})
    g = _gap(q)
    assert g["origins"] == {"widget": 1}
    assert g["last_kind"] == "answered" and g["last_role"] == "fast"
    assert g["last_answer"] == "It is black."
    assert g["last_pages"] == [{"source": "Review.pdf", "pages": [3, 4]}]


def test_refused_turn_and_origin_counts_accumulate():
    q = "does the review unit take coins from mars"
    _ask(q, "widget", {"role": "rejected", "answer": "I couldn't find that.", "sources": []})
    _ask(q, "console", {"role": "rejected", "answer": "I couldn't find that.", "sources": []})
    g = _gap(q)
    assert g["origins"] == {"widget": 1, "console": 1}
    assert g["times_asked"] == 2
    assert g["last_kind"] == "refused"


def test_no_gap_this_turn_means_no_stamp():
    tok = pipeline_trace.start()
    try:
        main._stamp_gap({"role": "fast", "answer": "x"})   # no gap_id: a no-op
    finally:
        pipeline_trace.reset(tok)
    assert faq_store.stamp_gap({"x": 1}, gap_id="no-such-gap") is False


def test_harness_only_entries_are_hidden_by_default():
    q = "what is the capital of the review unit"
    _ask(q, "eval", {"role": "rejected", "answer": "No.", "sources": []})
    assert faq_store.is_test_only(_gap(q))
    shown = client.get("/faq/gaps?group_similar=false", headers=ADMIN).json()["gaps"]
    assert q not in [g["question"] for g in shown]
    shown = client.get("/faq/gaps?group_similar=false&include_tests=true",
                       headers=ADMIN).json()["gaps"]
    assert q in [g["question"] for g in shown]
    # Entries from before origins were stamped are real until shown otherwise.
    assert not faq_store.is_test_only({"question": "old"})
