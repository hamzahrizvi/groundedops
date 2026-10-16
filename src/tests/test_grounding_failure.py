"""A verifier outage must never be mistaken for grounded evidence."""
from unittest.mock import patch

import numpy as np

import grounding


def test_grounding_failure_is_explicit_and_closed():
    context = [{"text": "The product supports USB."}]
    with patch.object(grounding, "_get_nli_model",
                      side_effect=RuntimeError("model unavailable")):
        ok, score = grounding.check_grounding(
            "The product supports USB.", context)
        assert ok is False
        assert score is None
        assert grounding.score_unit("The product supports USB.", context) is None


def test_stream_grounder_refuses_when_verifier_is_unavailable():
    context = [{"text": "The product supports USB."}]
    grounder = grounding.StreamGrounder(context, threshold=0.55)
    with patch.object(grounding, "_get_nli_model",
                      side_effect=RuntimeError("model unavailable")):
        _, ok = grounder.feed("The product supports USB. Next")
    assert ok is False
    assert grounder.verifier_unavailable is True


class _NLI:
    def predict(self, pairs, apply_softmax=True):
        return np.array([[0.0, 0.9 if "ENTAILS" in p else 0.01, 0.0]
                         for p, _ in pairs])


def _nli():
    return (patch.object(grounding, "_get_nli_model", return_value=_NLI()),
            patch.object(grounding, "_entailment_index", return_value=1))


def test_extra_text_never_sinks_a_unit_the_passages_entail():
    answer = "The relay pin seven output is closed."
    context = [{"text": "Relay ENTAILS this fact for the board."}]
    extra = [{"text": f"The relay pin seven output is closed on board number {i} today."}
             for i in range(15)]
    a, b = _nli()
    with a, b:
        assert grounding.check_grounding(
            answer, context, threshold=0.35, extra_chunks=extra)[0] is True
        # round 1's fault: extra premises mixed in with the ranked ones push it out
        assert grounding.check_grounding(
            answer, context + extra, threshold=0.35)[0] is False


def test_extra_text_can_rescue_a_unit_the_passages_miss():
    answer = "The relay pin seven output is closed."
    context = [{"text": "The board has a green status light fitted."}]
    extra = [{"text": "Relay pin seven ENTAILS closed output on the board."}]
    a, b = _nli()
    with a, b:
        assert grounding.check_grounding(answer, context, threshold=0.35)[0] is False
        assert grounding.check_grounding(
            answer, context, threshold=0.35, extra_chunks=extra)[0] is True
