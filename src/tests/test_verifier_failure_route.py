"""The normal query route must not turn verifier downtime into an answer."""
import sys
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


def test_query_checks_the_cited_pages_but_cites_only_the_passages():
    chunk = {
        "id": "unit-1", "text": "The product supports USB.",
        "source": "Manual.pdf", "page": 1, "product": "product",
        "category": "", "rerank_score": 0.99,
    }
    sibling = dict(chunk, id="unit-2", text="USB 2.0 full speed.")
    grounded, lexical, grounded_extra = [], [], []

    def _ground(ans, chunks, **k):
        grounded.extend(c["id"] for c in chunks)
        grounded_extra.extend(c["id"] for c in (k.get("extra_chunks") or []))
        return (False, 0.1)

    def _lex(ans, chunks):
        lexical.extend(c["id"] for c in chunks)
        return True

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
         patch.object(sys.modules["retrieval_db"], "cited_pages",
                      return_value=[sibling], create=True), \
         patch.object(main, "check_grounding", side_effect=_ground), \
         patch.object(main, "_lexically_supported", side_effect=_lex), \
         patch.object(main, "_structures_for", return_value=[]), \
         patch.object(main.more_context, "build", return_value={"kind": "support"}), \
         patch.object(main, "log_interaction"):
        result = main.query(main.QueryRequest(
            q="Does the product support USB?", skip_faq=True))

    assert "unit-2" in grounded_extra and "unit-2" not in grounded
    assert "unit-1" in lexical and "unit-2" not in lexical
    assert all("unit-2" not in s.get("chunk_ids", []) for s in result["sources"])
