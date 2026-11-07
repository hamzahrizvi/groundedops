"""End-of-chat star ratings from the widget, and staff issue reports from
the console's Test chat (feedback_store.py)."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import _harness  # noqa: F401 -- stubs and the test token secret first
import main
import feedback_store
import quota
import widget_api
from fastapi.testclient import TestClient

client = TestClient(main.app)
SRC = Path(widget_api.__file__).parent
SUPPORT = {"x-admin-password": _harness.SUPPORT_TOKEN}
BASIC = {"x-admin-password": _harness.BASIC_TOKEN}


def _rate(sid, stars, comment="", ticket=None):
    return client.post("/widget/rating", json={
        "session_id": sid, "visitor_id": "rate-visitor", "stars": stars, "comment": comment,
        "rating_ticket": widget_api.rating_ticket(sid) if ticket is None else ticket})


def test_ratings_are_kept_one_per_chat_and_summarised():
    widget_api._remember_answer("rate-rid", "How do I clear a jam?", "nv200s", "rate-1")
    assert _rate("rate-1", 5).status_code == 200
    assert _rate("rate-1", 2, "Too slow, answer was wrong").status_code == 200   # replaces the 5
    assert _rate("rate-2", 1, "slow and the wrong manual").status_code == 200
    assert _rate("rate-3", 6).status_code == 422
    assert _rate("rate-3", 0).status_code == 422
    r = client.get("/admin/feedback", headers=SUPPORT).json()["ratings"]
    assert r["count"] == 2 and r["average"] == 1.5, r
    assert r["spread"]["2"] == 1 and r["spread"]["5"] == 0, r
    assert {t["word"] for t in r["themes"]} >= {"slow", "wrong"}, r["themes"]


def test_a_tester_report_reaches_the_feedback_page():
    body = {"note": "It quoted the NV9 temperature, not the NV200's",
            "conversation": [{"q": "operating temp?", "a": "0 to 50 C"}], "product": "nv200s"}
    assert client.post("/admin/feedback/report", json=body).status_code == 401
    r = client.post("/admin/feedback/report", json=body, headers=BASIC)   # basic accounts test too
    assert r.status_code == 200, r.text
    assert client.post("/admin/feedback/report", headers=BASIC,
                       json=dict(body, conversation=[])).status_code == 422
    assert client.get("/admin/feedback", headers=BASIC).status_code == 403
    reps = client.get("/admin/feedback", headers=SUPPORT).json()["reports"]
    rep = next(x for x in reps if x["id"] == r.json()["id"])
    assert rep["conversation"] == body["conversation"] and rep["reporter"], rep
    assert client.delete("/admin/feedback/report/" + rep["id"], headers=SUPPORT).status_code == 200
    assert client.delete("/admin/feedback/report/" + rep["id"], headers=SUPPORT).status_code == 404


def test_the_widget_asks_for_a_rating_and_the_console_reports():
    js = (SRC / "widget" / "groundedops-widget.js").read_text(encoding="utf-8")
    assert "rating_ticket: state.ratingTicket" in js and "!state.ratingTicket" in js
    assert "askRating(close)" in js and "askRating(startFresh)" in js and "/widget/rating" in js
    assert 'hasAttr("data-no-rating")' in js   # the Test chat never asks
    assert 'data-no-rating' in (SRC / "widget" / "preview.html").read_text(encoding="utf-8")
    html = (SRC / "admin.html").read_text(encoding="utf-8")
    assert "/admin/feedback/report" in html and "viewFeedback()" in html


def test_the_ratings_store_keeps_only_the_newest_rows():
    saved = (feedback_store.RATINGS_PATH, feedback_store.RATINGS_MAX)
    feedback_store.RATINGS_PATH = os.path.join(tempfile.mkdtemp(prefix="ratings-cap-"), "r.json")
    feedback_store.RATINGS_MAX = 3
    try:
        for n in range(5):
            feedback_store.save_rating(f"cap-{n}", 4, "", None, [])
        data = feedback_store.jsonstore.load(feedback_store.RATINGS_PATH, {})
        assert set(data) == {"cap-2", "cap-3", "cap-4"}, sorted(data)
    finally:
        feedback_store.RATINGS_PATH, feedback_store.RATINGS_MAX = saved


def _guest_ask(path, sid):
    with patch.object(quota, "anon_llm_enabled", return_value=False), \
         patch.object(widget_api.faq_store, "suggest_candidates", return_value={"mode": "none"}):
        return client.post(path, json={"q": "How do I clear a coin jam?",
                                       "session_id": sid, "visitor_id": "ticket-guest"})


def test_only_answered_chats_can_be_rated_so_fakes_cannot_push_real_ones_out():
    saved = (feedback_store.RATINGS_PATH, feedback_store.RATINGS_MAX)
    feedback_store.RATINGS_PATH = os.path.join(tempfile.mkdtemp(prefix="ratings-ticket-"), "r.json")
    feedback_store.RATINGS_MAX = 3
    try:
        r = _guest_ask("/widget/ask", "real-chat-1")
        assert r.status_code == 200
        ticket = r.json()["rating_ticket"]
        assert ticket and ticket == widget_api.rating_ticket("real-chat-1")
        s = _guest_ask("/widget/ask/stream", "real-chat-1")
        assert '"rating_ticket": "' + ticket + '"' in s.text
        assert _rate("real-chat-1", 2, "real", ticket=ticket).status_code == 200
        for n in range(5):
            r = client.post("/widget/rating", json={"session_id": f"fake-{n}",
                                                    "visitor_id": f"fake-v{n}", "stars": 5})
            assert r.status_code == 403
        data = feedback_store.jsonstore.load(feedback_store.RATINGS_PATH, {})
        assert set(data) == {"real-chat-1"} and data["real-chat-1"]["stars"] == 2
    finally:
        feedback_store.RATINGS_PATH, feedback_store.RATINGS_MAX = saved


def test_a_ticket_for_one_chat_does_not_rate_another():
    saved = feedback_store.RATINGS_PATH
    feedback_store.RATINGS_PATH = os.path.join(tempfile.mkdtemp(prefix="ratings-ticket-"), "r.json")
    try:
        assert _rate("chat-b", 4, ticket=widget_api.rating_ticket("chat-b")).status_code == 200
        assert _rate("chat-b", 1, "forged", ticket=widget_api.rating_ticket("chat-a")).status_code == 403
        assert _rate("chat-b", 1, ticket="").status_code == 403
        assert _rate("chat-b", 1, ticket="0" * 64).status_code == 403
        row = feedback_store.jsonstore.load(feedback_store.RATINGS_PATH, {})["chat-b"]
        assert row["stars"] == 4 and row["comment"] == ""
    finally:
        feedback_store.RATINGS_PATH = saved


def test_a_chat_that_got_no_answer_cannot_be_rated():
    saved = feedback_store.RATINGS_PATH
    feedback_store.RATINGS_PATH = os.path.join(tempfile.mkdtemp(prefix="ratings-ticket-"), "r.json")
    try:
        d = client.post("/widget/chat").json()
        assert _rate(d["session_id"], 5, ticket="").status_code == 403
        assert _rate(d["session_id"], 5, ticket=d["delete_token"]).status_code == 403
        assert widget_api.rating_ticket(d["session_id"]) != d["delete_token"]
        assert d["session_id"] not in feedback_store.jsonstore.load(feedback_store.RATINGS_PATH, {})
    finally:
        feedback_store.RATINGS_PATH = saved


def test_ratings_have_their_own_daily_limit():
    with patch.dict(quota.PUBLIC_WRITE_LIMITS, {"rating": (1, 100)}):
        body = {"session_id": "own-1", "visitor_id": "rating-bucket-visitor", "stars": 4,
                "rating_ticket": widget_api.rating_ticket("own-1")}
        assert client.post("/widget/rating", json=body).status_code == 200
        body["session_id"] = "own-2"
        body["rating_ticket"] = widget_api.rating_ticket("own-2")
        assert client.post("/widget/rating", json=body).status_code == 429
