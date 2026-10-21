"""D-20261020-06: the id-like fields of the public widget models are capped."""
import uuid

import _harness  # noqa: F401 -- stubs and the test token secret first
import main
import widget_api
from fastapi.testclient import TestClient

client = TestClient(main.app)
LONG = "x" * 10000
ASK_FIELDS = ("session_id", "visitor_id", "product", "category", "faq_id", "language", "effort")


def test_widget_minted_values_pass_the_models():
    sid, vid = str(uuid.uuid4()), str(uuid.uuid4())
    widget_api.AskRequest(q="hi", session_id=sid, visitor_id=vid, faq_id=str(uuid.uuid4()),
                          product="biometrics_general", category="biometrics",
                          effort="faq_only", language="en")
    widget_api.FeedbackRequest(request_id="r", vote="up", session_id=sid, visitor_id=vid)
    widget_api.DraftRequest(kind="sales", visitor_id=vid, product="biometrics_general", source="chat")
    edge = "y" * widget_api.MAX_ID_CHARS
    assert widget_api.AskRequest(q="hi", session_id=edge).session_id == edge


def test_over_long_ids_get_422_at_the_edge():
    for field in ASK_FIELDS:
        r = client.post("/widget/ask", json={"q": "hi", field: LONG})
        assert r.status_code == 422, f"/widget/ask {field}: {r.status_code}"
    r = client.post("/widget/ask/stream", json={"q": "hi", "product": LONG})
    assert r.status_code == 422, f"/widget/ask/stream product: {r.status_code}"
    for field in ("session_id", "visitor_id"):
        r = client.post("/widget/feedback", json={"request_id": "r", "vote": "up", field: LONG})
        assert r.status_code == 422, f"/widget/feedback {field}: {r.status_code}"
    r = client.post("/widget/draft_enquiry", json={"kind": "sales", "product": LONG})
    assert r.status_code == 422, f"/widget/draft_enquiry product: {r.status_code}"


def test_a_widget_shaped_vote_still_works():
    sid = str(uuid.uuid4())
    widget_api._remember_answer("rid-cap-ok", "q", None, sid)
    r = client.post("/widget/feedback", json={
        "request_id": "rid-cap-ok", "vote": "up", "session_id": sid,
        "visitor_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
