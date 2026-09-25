"""Pure tests for eval.py's layered, repeatable gate helpers."""
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
