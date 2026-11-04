"""A fix-it ask puts the fault's steps first in the prompt (D-20261029-03)."""
import sys
from unittest.mock import patch

from text_utils import is_refusal
import _harness  # noqa: F401 -- load main with lightweight test dependencies
import main

_BG = {"id": "bg", "text": "CASHBOX TEXT", "source": "m.pdf", "page": 1, "product": "",
       "category": "", "rerank_score": 0.98, "section": "Options › Cashbox Options"}
_FIX = {"id": "fx", "text": "LOZENGE STEPS", "source": "m.pdf", "page": 2, "product": "",
        "category": "", "rerank_score": 0.30, "section": "Maint › Clearing a Jam"}


def _ask(msg, scoped):
    main.APP_STATE["ready"] = True
    ranked = [dict(_BG), dict(_FIX)]
    scope = ({"product": "bv30"}, "bv30") if scoped else ({}, None)
    with patch.object(main, "retrieve_fused", return_value=[dict(_BG), dict(_FIX)]), \
         patch.object(main, "retrieve_from_db", return_value=[dict(_BG), dict(_FIX)]), \
         patch.object(main, "rerank", side_effect=lambda q, r, top_k=5: ranked[:top_k]), \
         patch.object(main, "_resolve_question_scope", return_value=scope), \
         patch.object(main, "fix_sections", return_value=[dict(_FIX)]) as fs, \
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
        main.query(main.QueryRequest(q=msg, skip_faq=True), None)
    return gen, fs


def test_scoped_fix_it_ask_puts_the_steps_first():
    gen, fs = _ask("note stuck in our bv30, how do i get it out?", True)
    prompt = gen.call_args_list[0][0][1]
    assert fs.called
    assert 0 <= prompt.index("LOZENGE STEPS") < prompt.index("CASHBOX TEXT")


def test_unscoped_fix_it_ask_is_untouched():
    _, fs = _ask("note stuck in our bv30, how do i get it out?", False)
    assert not fs.called


def test_scoped_non_fix_ask_is_untouched():
    _, fs = _ask("what does the escrow do on the bv30?", True)
    assert not fs.called


def test_scoped_bare_symptom_report_gets_the_fault_steps():
    gen, fs = _ask("plugged our bv30 in by usb, led is on but the pc search finds "
                   "nothing after 2 tries", True)
    prompt = gen.call_args_list[0][0][1]
    assert fs.called
    assert 0 <= prompt.index("LOZENGE STEPS") < prompt.index("CASHBOX TEXT")
