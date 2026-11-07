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


def _rate(sid, stars, comment=""):
    return client.post("/widget/rating", json={
        "session_id": sid, "visitor_id": "rate-visitor", "stars": stars, "comment": comment})


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


def test_ratings_have_their_own_daily_limit():
    with patch.dict(quota.PUBLIC_WRITE_LIMITS, {"rating": (1, 100)}):
        body = {"session_id": "own-1", "visitor_id": "rating-bucket-visitor", "stars": 4}
        assert client.post("/widget/rating", json=body).status_code == 200
        body["session_id"] = "own-2"
        assert client.post("/widget/rating", json=body).status_code == 429
