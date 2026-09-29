"""8.11: /widget/ask/stream reports the pipeline's real stages while it works.

The pipeline itself is faked (main.query_any_language), but everything
around it is real: _traced_query opens the trace, run_in_threadpool carries
the listener into the worker thread, and pipeline_trace.mark() hands each
stage back to the event loop.
"""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import _harness  # noqa: F401 -- stubs and the test token secret first
import main
import pipeline_trace as ptrace
import widget_api
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_live  # noqa: E402

client = TestClient(main.app)
ANSWER = ".NET is not needed. Is it quick?\n?Yes.\n| a | b |\n|---|---|"


def _fake_pipeline(payload, x_user_id=None):
    for stage in ("condense", "retrieve", "rerank", "procedures", "generate", "ground"):
        ptrace.mark(stage)
        time.sleep(0.03)
    return {"answer": ANSWER, "sources": [], "role": "fast", "request_id": "r811"}


def _events(q="How long is the warranty?", sid="live-811"):
    main.APP_STATE["ready"] = True
    with patch.object(main, "query_any_language", _fake_pipeline), \
         patch.object(main, "log_interaction", lambda *a, **k: None):
        r = client.post("/widget/ask/stream", json={"q": q, "session_id": sid},
                        headers={"Authorization": "Bearer " + run_live.member_token(sid)})
    assert r.status_code == 200, r.text
    out = []
    for frame in r.text.split("\n\n"):
        lines = dict(l.split(": ", 1) for l in frame.splitlines() if ": " in l)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_real_stages_arrive_before_the_answer_in_order():
    ev = _events()
    kinds = [k for k, _ in ev]
    first_meta = kinds.index("meta")
    statuses = [d for k, d in ev[:first_meta] if k == "status"]
    assert statuses[0]["id"] == "entry", "an immediate first event"
    ids = [d["id"] for d in statuses[1:]]
    assert ids == ["condense", "retrieve", "procedures", "generate"], ids
    assert [d["stage"] for d in statuses[1:]] == [
        widget_api.PROGRESS_LINES[i] for i in ids]
    at = [d["at_ms"] for d in statuses[1:]]
    assert at == sorted(at) and at[-1] > 0, "each stage says when it happened"


def test_the_answer_survives_the_sentence_split_intact():
    ev = _events()
    text = "".join(d["text"] for k, d in ev if k == "delta")
    assert text == ANSWER.strip()


def test_the_meta_event_carries_what_the_widget_reads():
    meta = next(d for k, d in _events() if k == "meta")
    for key in ("request_id", "role", "needs_sign_in", "service_degraded",
                "more_context", "suggested_replies", "offer_support"):
        assert key in meta, key
    assert meta["request_id"] == "r811"


def test_a_mark_outside_a_listener_costs_nothing():
    tok = ptrace.start()
    try:
        ptrace.mark("retrieve")
        assert ptrace.snapshot()["stages"][0]["id"] == "retrieve"
    finally:
        ptrace.reset(tok)
