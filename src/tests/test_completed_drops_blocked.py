"""Passages added after selection obey the blocked-product rule (D-20261106-15)."""
import sys
from unittest.mock import patch

from text_utils import is_refusal
import _harness  # noqa: F401 -- load main with lightweight test dependencies
import catalog
import main

_CUR = {"id": "c1", "text": "CURRENT TEXT", "source": "cur.pdf", "page": 1, "product": "newp",
        "category": "", "rerank_score": 0.95, "section": "Faults"}
_OLD = {"id": "o1", "text": "OLD FLASH TABLE", "source": "old.pdf", "page": 1, "product": "oldp",
        "category": "", "rerank_score": 0.1}
_SHARED = {"id": "s1", "text": "SHARED TABLE", "source": "shared.pdf", "page": 1,
           "product": "oldp,newp", "category": "", "rerank_score": 0.1}


def _ask(blocked):
    main.APP_STATE["ready"] = True
    ranked = [dict(_CUR)]
    with patch.object(main, "retrieve_fused", return_value=[dict(_CUR)]), \
         patch.object(main, "retrieve_from_db", return_value=[dict(_CUR)]), \
         patch.object(main, "rerank", side_effect=lambda q, r, top_k=5: ranked[:top_k]), \
         patch.object(main, "_resolve_question_scope", return_value=({}, None)), \
         patch.object(main, "fix_sections", return_value=[]), \
         patch.object(main, "complete_procedures",
                      side_effect=lambda chunks, *a, **k: list(chunks) + [dict(_OLD), dict(_SHARED)]), \
         patch.object(catalog, "blocked_products", return_value=set(blocked)), \
         patch.object(main.faq_store, "suggest_candidates", return_value={"mode": "none"}), \
         patch.object(main, "route_model", return_value=("accurate", ("local", "mistral"))), \
         patch.object(main, "generate_with_fallback", return_value={
             "text": "ok", "model": "mistral", "provider": "local"}) as gen, \
         patch.object(main, "check_grounding", return_value=(True, 0.97)), \
         patch.object(main, "_structures_for", return_value=[]), \
         patch.object(main.more_context, "build", return_value={"kind": "support"}), \
         patch.object(main, "log_interaction"), \
         patch.object(main, "is_refusal", is_refusal), \
         patch.object(sys.modules["text_utils"], "is_refusal", is_refusal, create=True):
        main.query(main.QueryRequest(q="what does the error table say?", skip_faq=True), None)
    return gen.call_args_list[0][0][1]


def test_a_blocked_products_passage_added_after_selection_is_dropped():
    p = _ask({"oldp"})
    assert "CURRENT TEXT" in p
    assert "SHARED TABLE" in p
    assert "OLD FLASH TABLE" not in p


def test_nothing_blocked_keeps_every_added_passage():
    p = _ask(set())
    assert "OLD FLASH TABLE" in p and "SHARED TABLE" in p


def test_drop_hidden_is_the_visible_rule():
    with patch.object(catalog, "blocked_products", return_value={"oldp"}):
        out = main._drop_hidden([dict(_OLD), dict(_SHARED), dict(_CUR),
                                 {"source": "x.pdf", "product": ""}])
    assert [r["source"] for r in out] == ["shared.pdf", "cur.pdf", "x.pdf"]
