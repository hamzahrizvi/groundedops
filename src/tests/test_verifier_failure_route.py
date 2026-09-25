"""The normal query route must not turn verifier downtime into an answer."""
from unittest.mock import patch

import _harness  # noqa: F401 -- installs the lightweight main.py stubs first
import main


def test_query_refuses_explicitly_when_the_verifier_is_unavailable():
    chunk = {
        "id": "unit-1", "text": "The product supports USB.",
        "source": "Manual.pdf", "page": 1, "product": "product",
        "category": "", "rerank_score": 0.99,
    }
    main.APP_STATE["ready"] = True

    with patch.object(main, "retrieve_from_db", return_value=[chunk]), \
         patch.object(main, "rerank", return_value=[chunk]), \
         patch.object(main.faq_store, "suggest_candidates",
                      return_value={"mode": "none"}), \
         patch.object(main, "route_model",
                      return_value=("accurate", ("local", "mistral"))), \
         patch.object(main, "generate_with_fallback",
                      return_value={"text": "The product supports USB.",
                                    "model": "mistral", "provider": "local"}), \
         patch.object(main, "check_grounding", return_value=(False, None)), \
         patch.object(main, "_lexically_supported", return_value=True), \
         patch.object(main, "_structures_for", return_value=[]), \
         patch.object(main.more_context, "build", return_value={"kind": "support"}), \
         patch.object(main, "log_interaction"):
        result = main.query(main.QueryRequest(
            q="Does the product support USB?", skip_faq=True))

    assert result["verifier_unavailable"] is True
    assert result["flagged"] is True
    assert result["offer_support"] is True
    assert "could not verify an answer" in result["answer"].lower()
    assert result["grounding_score"] is None
