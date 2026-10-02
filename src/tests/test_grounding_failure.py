"""A verifier outage must never be mistaken for grounded evidence."""
from unittest.mock import patch

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
