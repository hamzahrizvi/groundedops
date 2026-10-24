"""D-20261023-04: a copied checklist is served only for a first-hand ask for
a procedure, from a relevant passage headed by what was asked. No pytest
fixtures: this file runs in its own process (the harness stubs main's deps)."""
import contextvars
from unittest.mock import MagicMock, patch

import _harness  # noqa: F401 -- installs the lightweight main.py stubs first
import main
import pipeline_trace


def _as_local(fn, *args, **kwargs):
    ctx = contextvars.copy_context()
    ctx.run(pipeline_trace.set_local_caller, True)
    return ctx.run(fn, *args, **kwargs)


CHUNK = {
    "id": "u-1",
    "text": ("Check the power connection is stable\n"
             "Verify the network cable is connected\n"
             "Confirm the hub IP is reachable\n"
             "Test the MyConnect app on tablet\n"
             "Ensure device registration completed"),
    "source": "Manual.pdf", "page": 1, "product": "product", "category": "",
    "section": "Connecting the tablet to the hub", "rerank_score": 0.99,
}


def _ask(q, chunk=CHUNK):
    main.APP_STATE["ready"] = True
    mock = MagicMock(return_value="- a\n- b\n- c")
    gen = MagicMock(return_value={"text": "The hub weighs 1 kg.",
                                  "model": "mistral", "provider": "local"})
    with patch.object(main, "retrieve_from_db", return_value=[chunk]), \
         patch.object(main, "rerank", return_value=[chunk]), \
         patch.object(main.faq_store, "suggest_candidates",
                      return_value={"mode": "none"}), \
         patch.object(main, "route_model",
                      return_value=("extract", ("local", "mistral"))), \
         patch.object(main, "generate_with_fallback", gen), \
         patch.object(main, "check_grounding", return_value=(True, 0.97)), \
         patch.object(main, "_structures_for", return_value=[]), \
         patch.object(main.more_context, "build", return_value={"kind": "support"}), \
         patch.object(main, "log_interaction"), \
         patch.object(main, "extract_structured_block", mock):
        req = main.QueryRequest(q=q, session_id=None, skip_faq=True)
        res = _as_local(main.query_route, req)
    return res["pipeline"], mock, gen


def test_a_procedure_ask_gets_the_copied_list():
    pipe, mock, _ = _ask("how do I connect the tablet to the hub?")
    assert pipe["exit"]["id"] == "extract"
    assert mock.called


def test_a_lookup_goes_to_the_model():
    pipe, mock, gen = _ask("how heavy is the hub?")
    assert pipe["exit"]["id"] != "extract"
    assert not mock.called
    assert gen.called


def test_a_list_headed_by_something_else_is_not_copied():
    pipe, mock, _ = _ask("how do I connect the tablet to the hub?",
                         dict(CHUNK, section="Cleaning the product"))
    assert pipe["exit"]["id"] != "extract"
    assert not mock.called


def test_a_weak_match_is_not_called_confident():
    pipe, _, _ = _ask("how do I connect the tablet to the hub?",
                      dict(CHUNK, rerank_score=0.010))
    band = [s for s in pipe["stages"] if s["id"] == "band"][0]
    assert band["note"].startswith("weak match"), band["note"]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
