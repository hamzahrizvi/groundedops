"""D-20261030-03: the public question routes cap length, ignore caller keys,
and both go through the quota."""
import sys
from pathlib import Path
from unittest.mock import patch

import _harness  # noqa: F401 -- stubs and the test token secret first
import main
import quota
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_live  # noqa: E402


def _hdr(sid):
    return {"Authorization": "Bearer " + run_live.member_token(sid)}


def test_a_10000_character_question_gets_422_on_every_question_route():
    import pydantic
    import pytest
    main.APP_STATE["ready"] = True
    client = TestClient(main.app)
    long_q = "x" * 10000
    for path in ("/widget/ask", "/widget/ask/stream"):
        r = client.post(path, json={"q": long_q}, headers=_hdr("long-q"))
        assert r.status_code == 422, (path, r.status_code)
    for path, model in (("/query", main.QueryRequest),
                        ("/query/stream", main.StreamQueryRequest)):
        r = client.post(path, json={"q": long_q})
        if r.status_code in (404, 403):  # not reachable from the test socket
            with pytest.raises(pydantic.ValidationError):
                model(q=long_q)
        else:
            assert r.status_code == 422, (path, r.status_code)
    assert main.QueryRequest(q="y" * main.MAX_QUERY_CHARS)


def test_keys_and_provider_in_the_widget_body_are_not_used():
    main.APP_STATE["ready"] = True
    sid = "keys-sid"
    seen = []

    def fake(req, *a, **k):
        seen.append(req)
        return {"answer": "ok", "sources": [], "request_id": "rid-keys"}

    with patch.object(main, "_traced_query", fake), \
         patch.object(main, "log_interaction", lambda *a, **k: None), \
         patch.object(main, "add_to_memory", lambda *a, **k: None):
        r = TestClient(main.app).post("/widget/ask", json={
            "q": "How do I clear a jam?", "session_id": sid,
            "deepseek_api_key": "sk-caller-ds", "openai_api_key": "sk-caller-oa",
            "anthropic_api_key": "sk-caller-an",
            "force_provider": "caller-provider", "force_model": "caller-model"},
            headers=_hdr(sid))
    assert r.status_code == 200, r.text
    assert len(seen) == 1
    req = seen[0]
    assert req.deepseek_api_key is None and req.openai_api_key is None
    assert req.anthropic_api_key is None
    assert req.force_provider != "caller-provider"
    assert req.force_model != "caller-model"


def test_the_quota_gates_both_widget_question_routes():
    main.APP_STATE["ready"] = True
    sid = "quota-sid"
    calls = []
    denied = {"allowed": False, "reason": "daily_quota", "limit": 1,
              "remaining": 0, "reset_at": 0}
    with patch.object(quota, "check", return_value=denied), \
         patch.object(main, "_traced_query", lambda *a, **k: calls.append(1)):
        client = TestClient(main.app)
        r = client.post("/widget/ask", json={"q": "hello there", "session_id": sid},
                        headers=_hdr(sid))
        assert r.status_code == 429, r.text
        r = client.post("/widget/ask/stream", json={"q": "hello there", "session_id": sid},
                        headers=_hdr(sid))
        assert "event: error" in r.text and "429" in r.text, r.text
    assert not calls


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
