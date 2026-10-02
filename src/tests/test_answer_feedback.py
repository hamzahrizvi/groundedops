"""8.10: thumbs up/down on a widget answer.

A vote must name an answer this server gave in that conversation, is capped
by the contact form's limiter, is stored keyed by request_id, and a down
vote puts the question on the console's FAQs-from-customers page.
"""
import inspect
import json
import sys
from pathlib import Path
from unittest.mock import patch

import _harness  # noqa: F401 -- stubs and the test token secret first
import main
import faq_store
import logger as interaction_log
import quota
import widget_api
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_live  # noqa: E402

client = TestClient(main.app)
WIDGET_JS = (Path(widget_api.__file__).parent / "widget" / "groundedops-widget.js").read_text(encoding="utf-8")
SRC = "Accessing my device in Linux Environment-v2-20250224_144232 2.pdf"


def _vote(rid, vote="down", sid="fb-1", vid="fb-visitor"):
    return client.post("/widget/feedback", json={
        "request_id": rid, "vote": vote, "session_id": sid, "visitor_id": vid})


def test_a_real_widget_answer_can_be_voted_on():
    sid = "fb-real"
    main.APP_STATE["ready"] = True
    main._remember_steps_offer(sid, SRC)
    with patch.object(main, "_steps_answer", return_value="**Step 1: Do it**"), \
         patch.object(main, "log_interaction", lambda *a, **k: None), \
         patch.object(main, "add_to_memory", lambda *a, **k: None):
        r = client.post("/widget/ask", json={"q": "Yes, show me the steps", "session_id": sid},
                        headers={"Authorization": "Bearer " + run_live.member_token(sid)})
    rid = r.json()["request_id"]
    assert rid, "the widget answer carries its request_id"
    assert _vote(rid, "up", sid=sid).status_code == 200


def test_unknown_or_missing_ids_are_refused():
    assert _vote("not-a-real-id").status_code == 404
    r = client.post("/widget/feedback", json={"vote": "up"})
    assert r.status_code == 422
    widget_api._remember_answer("rid-other", "q", None, "someone-else")
    assert _vote("rid-other", sid="fb-1").status_code == 404, \
        "a vote from another conversation is refused"
    widget_api._remember_answer("rid-bad", "q", None, "fb-1")
    assert client.post("/widget/feedback", json={
        "request_id": "rid-bad", "vote": "meh", "session_id": "fb-1"}).status_code == 422


def test_a_down_vote_is_stored_logged_and_flags_the_question():
    q = "How do I reset the fingerprint reader on the MyCheckr?"
    widget_api._remember_answer("rid-down", q, "mycheckr", "fb-1",
                                "Hold the button for five seconds.")
    faq_store.record_gap(q, "mycheckr")          # asked once already
    logged = []
    with patch.object(interaction_log, "log_feedback",
                      lambda rid, vote, sid=None: logged.append((rid, vote))):
        assert _vote("rid-down", "down").status_code == 200
    stored = json.load(open(widget_api._FEEDBACK_PATH, encoding="utf-8"))
    assert stored["rid-down"]["vote"] == "down"
    assert stored["rid-down"]["question"] == q
    assert logged == [("rid-down", "down")]
    gap = next(g for g in faq_store.list_gaps() if g["question"] == q)
    assert gap["flagged_by_visitor"] == 1
    assert gap["times_asked"] == 1, "a vote is not another ask"
    assert gap["reason"] == "visitor_flagged"
    # 10.2: the reviewer sees what the visitor was shown.
    assert gap["flagged_answer"] == "Hold the button for five seconds."
    assert stored["rid-down"]["answer"] == "Hold the button for five seconds."


def test_the_logger_mirror_row_has_the_outcome():
    # _harness stubs logger, so read the real module's source.
    src = Path(widget_api.__file__).with_name("logger.py").read_text(encoding="utf-8")
    assert '"outcome":    "voted_" + vote' in src and '"request_id": request_id' in src


def test_votes_are_capped_by_the_shared_limiter():
    with patch.dict(quota.PUBLIC_WRITE_LIMITS, {"feedback": (2, 100)}):
        codes = []
        for i in range(3):
            widget_api._remember_answer(f"rid-cap{i}", "q", None, "fb-cap")
            codes.append(_vote(f"rid-cap{i}", "up", sid="fb-cap", vid="capper").status_code)
    assert codes == [200, 200, 429]


def test_the_response_and_the_widget_carry_what_a_vote_needs():
    src = inspect.getsource(widget_api.register)
    assert '"request_id": result.get("request_id")' in src, "/ask dict"
    meta = src[src.index('yield sse("meta"'):]
    assert '"request_id"' in meta[:600], "stream meta"
    assert "/widget/feedback" in WIDGET_JS and "function voteRow" in WIDGET_JS
    assert "msg.vote" in WIDGET_JS, "the vote is kept on the message for a resumed chat"
