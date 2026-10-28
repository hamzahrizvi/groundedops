"""D-20261010-17 self_route: a product chat's refusal reads the whole manual once.

The widened answer is served only if it passes the checks; otherwise the
turn ends exactly as it would have without the read.
"""
import sys
from contextlib import ExitStack
from unittest.mock import patch

import _harness  # noqa: F401 -- installs the lightweight main.py stubs first
import main
import pipeline_trace

REFUSED = "I could not find that in the knowledge base."
ANSWER = "The bezel is removed with a 2 mm hex key."
CHUNK = {"id": "m-1", "text": "Cleaning the note path.", "source": "Manual.pdf",
         "page": 3, "product": "nv9usb", "category": "", "rerank_score": 0.9}
BUNDLE = [{"id": "m-9", "text": "Remove the bezel with a 2 mm hex key.",
           "source": "Manual.pdf", "page": 40, "product": "nv9usb"}]
CITED = []


def _refusal(text):
    return "could not find" in (text or "").lower()


def _ask(replies, *, product="nv9usb", band="confident", grounded=True,
         bundle=BUNDLE, policy=None, tokens=None):
    """Run one turn; replies are what each generate call returns, in order."""
    CITED.clear()
    calls = []

    def gen(role, prompt, **kw):
        calls.append(prompt)
        out = {"text": replies[min(len(calls), len(replies)) - 1],
               "model": "m", "provider": "openai"}
        if tokens is not None:
            out["tokens"] = tokens[min(len(calls), len(tokens)) - 1]
        return out

    over = dict(policy or {})
    saved = main.policy.value
    main.APP_STATE["ready"] = True
    rdb = sys.modules["retrieval_db"]
    tu = sys.modules["text_utils"]
    with ExitStack() as st:
        for target, name, value in [
            (main, "retrieve_from_db", lambda *a, **k: [CHUNK]),
            (main, "rerank", lambda *a, **k: [CHUNK]),
            (main, "retrieval_confidence_band", lambda *a, **k: band),
            (main.faq_store, "suggest_candidates", lambda *a, **k: {"mode": "none"}),
            (main.faq_store, "record_gap", lambda *a, **k: None),
            (main, "route_model", lambda q: ("accurate", ("openai", "m"))),
            (main, "generate_with_fallback", gen),
            (main, "check_grounding", lambda *a, **k: (grounded, 0.9 if grounded else 0.1)),
            (main, "_lexically_supported", lambda *a, **k: False),
            (main, "_llm_verified", lambda *a, **k: False),
            (main, "_structures_for", lambda *a, **k: []),
            (main, "_vague_fault_checklist", lambda *a, **k: None),
            (main, "is_refusal", _refusal),
            (tu, "is_refusal", _refusal),
            (main.mentions, "enabled", lambda: False),
            (main.more_context, "build", lambda *a, **k: {"kind": "support"}),
            (main, "log_interaction", lambda *a, **k: None),
            (rdb, "product_bundle", lambda *a, **k: list(bundle)),
            (rdb, "bundle_evidence", lambda b, a, have: list(b)),
            (rdb, "cited_pages", lambda chunks, budget: CITED.append([c.get("id") for c in chunks]) or []),
            (rdb, "PERFORMANCE_BUDGET_CHARS", 400_000),
        ]:
            st.enter_context(patch.object(target, name, value, create=True))
        main.policy.value = lambda k: over[k] if k in over else saved(k)
        token = pipeline_trace.start()
        try:
            result = main.query(main.QueryRequest(
                q="How do I take the bezel off?", product=product,
                skip_faq=True))
            trace = pipeline_trace.snapshot() or {}
        finally:
            pipeline_trace.reset(token)
            main.policy.value = saved
    return result, calls, trace


def _outcome(trace):
    return (trace.get("meta") or {}).get("self_route")


def _stages(trace):
    return [s.get("id") for s in trace.get("stages", [])]


def test_widened_answer_that_verifies_is_served():
    result, calls, trace = _ask([REFUSED, ANSWER])
    assert result["answer"] == ANSWER
    assert len(calls) == 2
    assert "Remove the bezel" in calls[1] and "Remove the bezel" not in calls[0]
    assert "widen" in _stages(trace)
    assert _outcome(trace) == "answered"


def test_widened_answer_failing_the_check_restores_the_first_refusal():
    result, calls, trace = _ask([REFUSED, ANSWER, ANSWER, ANSWER], grounded=False)
    assert len(calls) == 2              # no grounding retry, no backup
    assert _outcome(trace) == "failed_checks"
    assert ANSWER not in result["answer"]
    assert result["offer_support"] is True


def test_widened_answer_that_still_refuses_leaves_the_refusal():
    result, calls, trace = _ask([REFUSED, REFUSED])
    assert len(calls) == 2
    assert _outcome(trace) == "still_refused"
    assert result["offer_support"] is True


def test_unscoped_chat_does_not_widen():
    result, calls, trace = _ask([REFUSED, ANSWER], product=None)
    assert len(calls) == 1
    assert "widen" not in _stages(trace)


def test_band_none_in_a_product_chat_reads_the_manual():
    result, calls, trace = _ask([ANSWER], band="none")
    assert len(calls) == 1 and "Remove the bezel" in calls[0]
    assert result["answer"] == ANSWER
    assert "widen" in _stages(trace)


def test_band_none_still_refused_returns_the_gate_reply():
    widened, calls, _ = _ask([REFUSED], band="none")
    assert len(calls) == 1
    old, none_calls, _ = _ask([ANSWER], band="none",
                              policy={"self_route": False})
    assert none_calls == []
    for k in ("answer", "role", "model", "reason", "offer_support", "sources"):
        assert widened[k] == old[k], k
    assert widened["role"] == "rejected"


def test_band_none_with_no_bundle_never_calls_the_model():
    result, calls, _ = _ask([ANSWER], band="none", bundle=[])
    assert calls == [] and result["role"] == "rejected"


def test_self_route_off_does_not_widen():
    result, calls, trace = _ask([REFUSED, ANSWER], policy={"self_route": False})
    assert len(calls) == 1
    assert "widen" not in _stages(trace)


def test_progress_line_for_the_widened_read():
    import widget_api
    assert widget_api.PROGRESS_LINES["widen"] == \
        "Widening the search to the whole manual"


def test_widened_turn_reads_the_evidence_pages_first():
    _ask([REFUSED, ANSWER])
    assert CITED and CITED[-1][0] == "m-9"


def test_normal_turn_keeps_the_passage_order_for_cited_pages():
    _ask([ANSWER])
    assert CITED and CITED[-1][0] == "m-1"


def test_token_cap_counts_the_widen_call_when_the_first_refusal_is_restored():
    result, calls, trace = _ask([REFUSED, ANSWER], grounded=False, tokens=[1000, 90000])
    assert len(calls) == 2 and _outcome(trace) == "failed_checks"
    assert result["total_tokens"] == 91000


def test_token_cap_counts_the_widen_call_on_the_nothing_matched_path():
    # band "none": the whole-manual read is the turn's only model call
    for replies, grounded in (([ANSWER], False), ([REFUSED], True)):
        result, calls, _ = _ask(replies, band="none", grounded=grounded, tokens=[90000])
        assert len(calls) == 1 and result["role"] == "rejected"
        assert result["total_tokens"] == 90000


def test_token_cap_counts_every_call_on_a_served_or_retried_turn():
    result, _, _ = _ask([REFUSED, ANSWER], tokens=[1000, 90000])
    assert result["answer"] == ANSWER and result["total_tokens"] == 91000
    result, calls, _ = _ask([ANSWER], product=None, grounded=False,
                            tokens=[1000], policy={"grounding_retries": 2})
    assert len(calls) == 3 and result["total_tokens"] == 3000
