"""D-20261108-01: the public ask routes act on a chat id only with the token
/widget/chat gave for it; without it the ask gets a fresh chat."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import _harness  # noqa: F401 -- stubs and the test token secret first
import main
import quota
import widget_api
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_live  # noqa: E402


def _hdr(sid):
    return {"Authorization": "Bearer " + run_live.member_token(sid)}


def _run(path, body, sid_for_hdr="proof-hdr", client=None):
    """POST once with a fake pipeline; returns (response, ids the pipeline saw,
    session ids the quota caps were called with)."""
    main.APP_STATE["ready"] = True
    seen, checked = [], []

    def fake(req, *a, **k):
        seen.append(req.session_id)
        return {"answer": "ok", "sources": [], "request_id": "rid-proof"}

    check, record = quota.session_check, quota.session_record

    def spy_check(*a, **k):
        checked.append(a[0] if a else None)
        return check(*a, **k)

    def spy_record(*a, **k):
        checked.append(a[0] if a else None)
        return record(*a, **k)

    with patch.object(main, "_traced_query", fake), \
         patch.object(main, "log_interaction", lambda *a, **k: None), \
         patch.object(main, "add_to_memory", lambda *a, **k: None), \
         patch.object(quota, "session_check", spy_check), \
         patch.object(quota, "session_record", spy_record):
        r = (client or TestClient(main.app)).post(path, json=body, headers=_hdr(sid_for_hdr))
    return r, seen, checked


def _chat():
    return TestClient(main.app).post("/widget/chat").json()


def _meta(text):
    lines = text.splitlines()
    i = lines.index("event: meta")
    return json.loads(lines[i + 1][len("data: "):])


def test_a_chat_id_without_its_token_gets_a_fresh_chat_on_ask():
    chat = _chat()
    sid = chat["session_id"]
    for extra in ({}, {"delete_token": "0" * 64}):
        r, seen, checked = _run("/widget/ask", {"q": "How do I clear a jam?", "session_id": sid, **extra})
        assert r.status_code == 200, r.text
        reply = r.json()
        assert seen and seen[0] != sid
        assert reply["session_id"] != sid
        assert reply["delete_token"] == widget_api.delete_token(reply["session_id"])
        assert checked and sid not in checked


def test_a_chat_id_with_its_token_is_answered_in_that_chat():
    chat = _chat()
    sid = chat["session_id"]
    r, seen, _ = _run("/widget/ask", {"q": "How do I clear a jam?", "session_id": sid,
                                      "delete_token": widget_api.delete_token(sid)})
    assert r.status_code == 200, r.text
    assert seen == [sid]
    assert "delete_token" not in r.json()


def test_the_stream_route_checks_the_token_the_same_way():
    sid = _chat()["session_id"]
    r, seen, _ = _run("/widget/ask/stream", {"q": "How do I clear a jam?", "session_id": sid})
    assert seen and seen[0] != sid
    meta = _meta(r.text)
    assert meta["session_id"] != sid
    assert meta["delete_token"] == widget_api.delete_token(meta["session_id"])
    r, seen, _ = _run("/widget/ask/stream", {"q": "How do I clear a jam?", "session_id": sid,
                                             "delete_token": widget_api.delete_token(sid)})
    assert seen == [sid]
    assert "delete_token" not in _meta(r.text)


def test_a_test_prefixed_id_from_this_box_needs_no_token():
    body = {"q": "How do I clear a jam?", "session_id": "live-proof-1"}
    local = TestClient(main.app, client=("127.0.0.1", 50000))
    _, seen, _ = _run("/widget/ask", body, client=local)
    assert seen == ["live-proof-1"]
    far = TestClient(main.app, client=("8.8.8.8", 50000))  # the harness lists "testclient" as private
    _, seen, _ = _run("/widget/ask", body, client=far)
    assert seen and seen[0] != "live-proof-1"


def test_the_widget_sends_and_adopts_the_token():
    js = (Path(__file__).resolve().parents[1] / "widget" / "groundedops-widget.js").read_text(encoding="utf-8")
    assert "delete_token: state.deleteToken" in js
    assert js.count("state.deleteToken = d.delete_token") >= 2


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
