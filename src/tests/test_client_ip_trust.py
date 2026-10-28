"""D-20261008-11/-14/-12: one trust rule for who is calling."""
import os
from types import SimpleNamespace as NS
from unittest.mock import patch

import _harness  # noqa: F401
import main
import pipeline_trace
import quota
from fastapi.testclient import TestClient
from guards import _external_ip, client_ip, is_local_caller
from pydantic import ValidationError
from widget_api import DraftRequest

FORGED = {"cf-connecting-ip": "1.2.3.4"}


def _req(host, headers=None):
    return NS(client=NS(host=host), headers=dict(headers or {}))


def test_a_forged_header_from_a_public_socket_is_ignored():
    assert client_ip(_req("203.0.113.5", FORGED)) == "203.0.113.5"
    assert _external_ip(_req("203.0.113.5", FORGED)) is None   # admin allowlist sees the socket


def test_the_same_header_from_loopback_is_honoured():
    assert client_ip(_req("127.0.0.1", FORGED)) == "1.2.3.4"


def test_trusted_proxies_lists_a_relay_and_bad_entries_are_skipped():
    relay = _req("198.51.100.7", FORGED)
    assert client_ip(relay) == "198.51.100.7"
    with patch.dict(os.environ, {"TRUSTED_PROXIES": "nonsense, 198.51.100.0/24"}):
        assert client_ip(relay) == "1.2.3.4"


def test_x_forwarded_for_needs_trust_proxy_and_a_trusted_peer():
    xff = {"x-forwarded-for": "1.2.3.4, 10.0.0.1"}
    with patch.dict(os.environ, {"TRUST_PROXY": ""}):
        assert client_ip(_req("127.0.0.1", xff)) == "127.0.0.1"
    with patch.dict(os.environ, {"TRUST_PROXY": "1"}):
        assert client_ip(_req("127.0.0.1", xff)) == "1.2.3.4"
        assert client_ip(_req("203.0.113.5", xff)) == "203.0.113.5"


def test_local_caller_means_no_proxy_header_and_a_private_socket():
    assert is_local_caller(_req("127.0.0.1"))
    assert not is_local_caller(_req("127.0.0.1", FORGED))     # the tunnel
    assert not is_local_caller(_req("203.0.113.5"))


QUERY_RESULT = {"answer": "ok", "role": "answer", "request_id": "r1", "sources": [],
                "needs_clarification": False, "clarification_options": [],
                "suggested_replies": [], "product_options": []}


def _origin_seen_by_the_pipeline(client, headers=None):
    seen = []

    def fake(payload, x_user_id=None):
        seen.append(pipeline_trace.origin_for(payload.session_id,
                                              pipeline_trace.get_meta("surface")))
        return dict(QUERY_RESULT)

    main.APP_STATE["ready"] = True
    auth = {"Authorization": "Bearer " + quota.issue_token("t-d11")}
    with patch.object(main, "query_any_language", side_effect=fake):
        r = client.post("/widget/ask", headers={**auth, **(headers or {})},
                        json={"q": "does it take coins", "session_id": "eval-d11",
                              "skip_faq": True})
    assert r.status_code == 200, r.text
    return seen[0]


def test_a_widget_session_named_eval_from_a_public_address_is_logged_as_widget():
    assert _origin_seen_by_the_pipeline(TestClient(main.app, client=("203.0.113.5", 50000))) == "widget"
    # through the tunnel: loopback socket, but a proxy header
    assert _origin_seen_by_the_pipeline(TestClient(main.app), FORGED) == "widget"


def test_the_same_session_from_the_box_is_still_a_test():
    # the harness lists TestClient's "testclient" socket in PRIVATE_NETWORKS
    assert _origin_seen_by_the_pipeline(TestClient(main.app)) == "eval"


def test_transcripts_are_bounded_on_the_lead_form_and_the_draft():
    client = TestClient(main.app)
    huge = [{"role": "user", "text": "x" * 100_001}]
    many = [{"role": "user", "text": "hi"}] * 51
    for t in (huge, many):
        r = client.post("/widget/lead", json={"kind": "support", "values": {}, "transcript": t})
        assert r.status_code == 422, r.status_code
        try:
            DraftRequest(kind="support", transcript=t)
            raise AssertionError("DraftRequest accepted an oversized transcript")
        except ValidationError:
            pass
