"""Pure tests for eval.py's layered, repeatable gate helpers."""
import sys
import types
from unittest.mock import patch

import eval as rag_eval


def test_case_key_is_layer_qualified():
    assert rag_eval.case_key({"q": "What is it?"}) == "faq::What is it?"
    assert (rag_eval.case_key({"q": "What is it?", "layer": "retrieval"})
            == "retrieval::What is it?")


def test_run_case_forwards_skip_faq_and_checks_sources():
    sent = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "answer": "The answer is in the manual.",
                "role": "accurate",
                "provider": "local",
                "sources": [{"source": "Manual.pdf"}],
            }

    def fake_post(url, json, timeout):
        sent.update(json)
        return Response()

    case = {
        "q": "Where is the answer?",
        "layer": "retrieval",
        "skip_faq": True,
        "outcome": "answered",
        "sources_any": ["manual.pdf"],
    }
    with patch.object(rag_eval.requests, "post", side_effect=fake_post):
        result = rag_eval.run_case(case, "session", do_grade=False)

    assert sent["skip_faq"] is True
    assert result["layer"] == "retrieval"
    assert result["checks"] == {"outcome": True, "sources_any": True}
    assert result["passed"] is True


def test_a_deflect_and_a_stated_non_answer_are_rejections():
    assert rag_eval.classify_outcome({"role": "sales", "answer": "I can only answer technical questions"}) == "rejected"
    assert rag_eval.classify_outcome({"role": "fast", "answer": "I don't have that", "answerability": "unanswerable"}) == "rejected"
    assert rag_eval.classify_outcome({"role": "fast", "answer": "It weighs 1.05 kg", "answerability": "stated"}) == "answered"
    assert rag_eval.classify_outcome({"role": "clarify", "needs_clarification": True}) == "clarify"


def test_a_friendly_refusal_is_rejected_even_though_role_stayed_answering():
    # INFERABLE/ADVISORY refusals never reset role away from fast/reasoning
    # (main.py's offer_support branches), so without the ported run_live.py
    # rule these scored "answered" purely because role was untouched.
    assert rag_eval.classify_outcome({
        "role": "fast", "answer": "I don't hold anything on that.",
        "answerability": "inferable", "offer_support": True, "sources": [],
    }) == "rejected"
    assert rag_eval.classify_outcome({
        "role": "reasoning", "answer": "I don't hold anything on that.",
        "answerability": "unanswerable", "offer_support": True,
        "sources": [{"source": "Manual.pdf"}],
    }) == "rejected"
    # offer_support with real sources and a non-unanswerable kind (the
    # capability/inference branches that DID answer) stays "answered".
    assert rag_eval.classify_outcome({
        "role": "fast", "answer": "Yes, that is documented.",
        "answerability": "inferable", "offer_support": False,
        "sources": [{"source": "Manual.pdf"}],
    }) == "answered"


def test_keywords_all_supports_any_of_lists_for_alternate_phrasing():
    case = {"q": "duration?", "keywords_all": [["1s", "1 second"], "relay"]}
    assert rag_eval._keyword_present("default relay duration is ~1s", case["keywords_all"][0])
    assert rag_eval._keyword_present("default relay duration is 1 second", case["keywords_all"][0])
    assert not rag_eval._keyword_present("default duration is 2 seconds", case["keywords_all"][0])


def test_llm_grade_no_longer_passes_on_a_bare_unparsed_word():
    fake_llm = types.SimpleNamespace(
        generate=lambda *a, **k: {"text": "I think this should pass overall."})
    with patch.dict(sys.modules, {"llm": fake_llm}):
        ok, reason = rag_eval.llm_grade("Q", "ref", "ans")
    assert ok is False
    assert reason.startswith("unparsed grader output")


def test_preflight_grader_aborts_on_empty_verdict():
    fake_llm = types.SimpleNamespace(generate=lambda *a, **k: {"text": ""})
    with patch.dict(sys.modules, {"llm": fake_llm}):
        assert rag_eval.preflight_grader() is False


def test_preflight_grader_passes_on_a_real_verdict_even_if_it_is_fail():
    fake_llm = types.SimpleNamespace(
        generate=lambda *a, **k: {"text": '{"verdict":"fail","reason":"wrong"}'})
    with patch.dict(sys.modules, {"llm": fake_llm}):
        assert rag_eval.preflight_grader() is True
