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


def _ask(log=None, faq=None, entry=None, **over):
    """Run one question through the ROUTE (not the body), which is what
    opens the trace, and hand back the response.

    `log` replaces main.log_interaction (default: a silent mock); `faq` is
    what suggest_candidates returns (default: no FAQ, and skip_faq=True);
    `entry` calls a different entry point with the request instead of the
    route (the widget's)."""
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
                      return_value=faq or {"mode": "none"}), \
         patch.object(main, "route_model", return_value=stubs["route_model"]), \
         patch.object(main, "generate_with_fallback",
                      return_value=stubs["generate_with_fallback"]), \
         patch.object(main, "check_grounding", return_value=stubs["check_grounding"]), \
         patch.object(main, "_structures_for", return_value=stubs["_structures_for"]), \
         patch.object(main.more_context, "build", return_value={"kind": "support"}), \
         patch.object(main, "log_interaction", side_effect=log):
        req = main.QueryRequest(
            q=over.get("q", "Does the product support USB?"),
            session_id=over.get("session_id"), skip_faq=faq is None)
        return (entry or main.query_route)(req)


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
    pipeline_trace.set_meta(origin="widget")            # nor this
    assert pipeline_trace.snapshot() is None


# ── M2: the log line knows who asked, how it ended, what checked it ──────

def _log_spy():
    """A log_interaction stand-in that records what the REAL logger would
    read from the trace at write time (logger._trace_fields reads the same
    snapshot), plus the arguments it was called with."""
    rows = []

    def spy(query, answer, role=None, *a, **k):
        snap = pipeline_trace.snapshot() or {}
        rows.append({"role": role, "request_id": k.get("request_id"),
                     "exit": (snap.get("exit") or {}).get("id"),
                     "meta": snap.get("meta") or {}})
    return rows, spy


def test_the_log_line_knows_the_session_origin_and_outcome():
    rows, spy = _log_spy()
    _ask(log=spy, session_id="eval-1234")
    assert len(rows) == 1
    row = rows[0]
    assert row["meta"]["session_id"] == "eval-1234"
    assert row["meta"]["origin"] == "eval"
    assert row["exit"] == "respond"
    assert row["meta"]["ground_via"] == "nli"
    assert row["meta"]["service_degraded"] is False
    assert row["request_id"]


def test_origin_is_who_asked_and_surface_is_the_door():
    assert pipeline_trace.origin_for("live-abc") == "live"
    assert pipeline_trace.origin_for("live-abc", "widget") == "live"
    assert pipeline_trace.origin_for("preflight-x") == "preflight"
    assert pipeline_trace.origin_for("3f2a-uuid") == "console"
    assert pipeline_trace.origin_for("3f2a-uuid", "widget") == "widget"
    assert pipeline_trace.origin_for(None) == "console"
    # A visitor on the widget is origin "widget"...
    rows, spy = _log_spy()
    _ask(log=spy, session_id="3f2a-visitor",
         entry=lambda req: main._traced_query(req, None, "widget"))
    assert rows[0]["meta"]["origin"] == "widget"
    assert rows[0]["meta"]["surface"] == "widget"
    # ...and run_live --path widget (M12) is a test on the widget surface,
    # not a customer.
    rows, spy = _log_spy()
    _ask(log=spy, session_id="live-11-abcd",
         entry=lambda req: main._traced_query(req, None, "widget"))
    assert rows[0]["meta"]["origin"] == "live"
    assert rows[0]["meta"]["surface"] == "widget"
    # /query is the "query" surface.
    rows, spy = _log_spy()
    _ask(log=spy)
    assert rows[0]["meta"]["surface"] == "query"
    assert rows[0]["meta"]["origin"] == "console"


def test_a_curated_faq_answer_now_writes_a_log_row():
    entry = {"id": "f1", "question": "Does the product support USB?",
             "answer": "Yes, over USB-C.", "source": "Manual.pdf"}
    rows, spy = _log_spy()
    out = _ask(log=spy, faq={"mode": "answer", "entry": entry})
    assert out["from_faq"] is True
    assert len(rows) == 1
    assert rows[0]["exit"] == "faq.answer"
    assert rows[0]["meta"]["ground_via"] == "faq"


def test_a_catalogue_answer_now_writes_a_log_row():
    rows, spy = _log_spy()
    with patch.object(main, "_sales_answer",
                      return_value={"answer": "The NV9 and NV200 run on 24V.",
                                    "kind": "catalogue"}):
        _ask(log=spy, q="Which of your validators run on 24V?")
    assert len(rows) == 1
    assert rows[0]["role"] == "catalogue"
    assert rows[0]["exit"] == "sales"
    assert rows[0]["request_id"]


def _real_logger():
    """_harness stubs `logger`; load the real module under another name."""
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "logger.py"
    spec = importlib.util.spec_from_file_location("_real_logger", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_real_logger_reads_the_trace_and_tolerates_its_absence():
    real = _real_logger()
    blank = real._trace_fields()
    assert set(blank) == {"session_id", "origin", "surface", "outcome", "verified_by",
                          "verifier", "service_degraded", "language"}
    assert all(v is None for v in blank.values())

    t = pipeline_trace.start()
    try:
        pipeline_trace.set_meta(session_id="eval-9", origin="eval",
                                ground_via="llm", verifier="SUPPORT: YES")
        pipeline_trace.mark("retrieve", "8 candidates")
        # a step along the way is not how the turn ended
        assert real._trace_fields()["outcome"] is None
        pipeline_trace.mark("respond", "1 source")
        f = real._trace_fields()
    finally:
        pipeline_trace.reset(t)
    assert f["outcome"] == "respond" and f["origin"] == "eval"
    assert f["verified_by"] == "llm" and f["verifier"] == "SUPPORT: YES"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
