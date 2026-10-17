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


def _route(lexical_ok, llm_ok):
    """One query with every check stubbed. Returns, per check_grounding call,
    (chunk ids, keyword names); the ids the number-match rescue saw; the ids
    each LLM-verifier call saw; and the result."""
    chunk = {
        "id": "unit-1", "text": "The product supports USB.",
        "source": "Manual.pdf", "page": 1, "product": "product",
        "category": "", "rerank_score": 0.99,
    }
    sibling = dict(chunk, id="unit-2", text="USB 2.0 full speed.")
    nli, lexical, llm = [], [], []

    def _ground(ans, chunks, **k):
        nli.append(([c["id"] for c in chunks], sorted(k)))
        return (False, 0.1)

    def _lex(ans, chunks):
        lexical.extend(c["id"] for c in chunks)
        return lexical_ok

    def _llm(ans, chunks, *a, **k):
        llm.append([c["id"] for c in chunks])
        return llm_ok

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
         patch.object(main, "_llm_verified", side_effect=_llm), \
         patch.object(main, "grounding_retries", return_value=0), \
         patch.object(main, "_structures_for", return_value=[]), \
         patch.object(main.more_context, "build", return_value={"kind": "support"}), \
         patch.object(main, "log_interaction"):
        result = main.query(main.QueryRequest(
            q="Does the product support USB?", skip_faq=True))
    return nli, lexical, llm, result


def test_only_the_llm_verifier_reads_the_cited_pages():
    # D-20261015-02 r5: NLI, the number match and citations see the passages only.
    nli, lexical, llm, result = _route(lexical_ok=False, llm_ok=False)
    assert nli and all("unit-2" not in ids and "extra_chunks" not in kw
                       for ids, kw in nli), nli
    assert llm and all("unit-1" in ids and "unit-2" in ids for ids in llm), llm
    assert "unit-1" in lexical and "unit-2" not in lexical
    assert all("unit-2" not in s.get("chunk_ids", []) for s in result["sources"])


def test_nli_runs_once_when_a_cheaper_check_passes():
    for lex_ok, llm_ok in ((True, False), (False, True)):
        nli, _, _, _ = _route(lexical_ok=lex_ok, llm_ok=llm_ok)
        assert len(nli) == 1, (lex_ok, llm_ok, nli)
