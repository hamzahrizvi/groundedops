"""The /query route must say WHERE a turn ended, not just how it ended.

The console's Pipeline check could show that a question failed but not
where: a refusal, a question back and a suppressed answer all arrive
looking alike, and telling them apart meant reading backend.log against a
1300-line function. They are four different faults with four different
fixes, so the route now reports the stages it passed through and the one
that decided the turn (pipeline_trace.py).
"""
from unittest.mock import patch

import _harness  # noqa: F401 -- installs the lightweight main.py stubs first
import main
import pipeline_trace


CHUNK = {
    "id": "unit-1", "text": "The product supports USB.",
    "source": "Manual.pdf", "page": 1, "product": "product",
    "category": "", "rerank_score": 0.99,
}


def _ask(**over):
    """Run one question through the ROUTE (not the body), which is what
    opens the trace, and hand back the response."""
    main.APP_STATE["ready"] = True
    stubs = {
        "retrieve_from_db": [CHUNK], "rerank": [CHUNK],
        "route_model": ("accurate", ("local", "mistral")),
        "generate_with_fallback": {"text": "The product supports USB.",
                                   "model": "mistral", "provider": "local"},
        "check_grounding": (True, 0.97),
        "_structures_for": [],
    }
    stubs.update(over)
    with patch.object(main, "retrieve_from_db", return_value=stubs["retrieve_from_db"]), \
         patch.object(main, "rerank", return_value=stubs["rerank"]), \
         patch.object(main.faq_store, "suggest_candidates",
                      return_value={"mode": "none"}), \
         patch.object(main, "route_model", return_value=stubs["route_model"]), \
         patch.object(main, "generate_with_fallback",
                      return_value=stubs["generate_with_fallback"]), \
         patch.object(main, "check_grounding", return_value=stubs["check_grounding"]), \
         patch.object(main, "_structures_for", return_value=stubs["_structures_for"]), \
         patch.object(main.more_context, "build", return_value={"kind": "support"}), \
         patch.object(main, "log_interaction"):
        return main.query_route(main.QueryRequest(
            q="Does the product support USB?", skip_faq=True))


def test_an_answered_turn_reports_the_path_it_took():
    trace = _ask().get("pipeline")
    assert trace, "the route must attach a pipeline trace"
    ids = [s["id"] for s in trace["stages"]]
    # The order is the order it ran in, and searching precedes answering.
    assert ids.index("retrieve") < ids.index("generate") < ids.index("respond")
    assert trace["exit"]["id"] == "respond"
    # Every stage carries wording a non-developer can read.
    assert all(s["label"] and s["label"] != s["id"] for s in trace["stages"])


def test_the_evidence_that_decided_it_is_recorded():
    """A stage without its numbers turns "it refused" into a shrug. The
    band's note has to carry the score and the thresholds it was judged
    against, because that is the difference between a tuning problem and
    a missing document."""
    trace = _ask()["pipeline"]
    band = [s for s in trace["stages"] if s["id"] == "band"][0]
    assert "0.99" in band["note"] and "gate" in band["note"]


def test_a_suppressed_answer_names_itself_as_the_exit():
    """The failure this whole file exists for: retrieval was fine, the
    model wrote something, and the verifier could not stand it up. That
    must not read the same as "nothing was found"."""
    trace = _ask(check_grounding=(False, 0.05))["pipeline"]
    assert trace["exit"]["id"] == "suppress", \
        f"expected the suppression to be the outcome, got {trace['exit']}"
    assert "manual does not support" in (trace["exit"]["note"] or "")
    # ...and the stages before it prove retrieval was NOT the problem.
    assert "retrieve" in [s["id"] for s in trace["stages"]]


def test_a_more_specific_outcome_beats_the_bare_reply():
    """"respond" only says something came back, which is true of a refusal
    too. Anything more specific recorded later wins."""
    t = pipeline_trace.start()
    try:
        pipeline_trace.mark("retrieve", "8 candidates")
        pipeline_trace.mark("respond", "1 source")
        pipeline_trace.mark("clarify", "which model?")
        assert pipeline_trace.snapshot()["exit"]["id"] == "clarify"
    finally:
        pipeline_trace.reset(t)


def test_tracing_never_costs_an_answer():
    """A diagnostic that can break the product is worse than no
    diagnostic. Outside a request there is nothing to record, and every
    call still has to be safe."""
    pipeline_trace.mark("retrieve", "no trace open")   # must not raise
    assert pipeline_trace.snapshot() is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
