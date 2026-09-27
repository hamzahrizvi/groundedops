"""run_live --path widget (M12) must drive the customer's surface for real.

Every live number used to come from /query, but a customer only ever
reaches /widget/ask, which hand-builds its own response dict. A key it
forgets is invisible to a /query-only run -- which is how 8.1 was once
recorded as shipped without ever reaching a customer. These tests pin the
instrument, not the backend: the runner signs in as a member, posts to
/widget/ask, names every allowlisted key the dict dropped, and reports a
disagreement with /query as one.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import _harness  # noqa: F401 -- stubs and the test token secret first
import main
import quota
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_live  # noqa: E402

# What /query decided for a handoff turn. The widget is fed this same dict.
QUERY_RESULT = {
    "answer": "Of course — I'll hand this over to a person.",
    "role": "handoff", "reason": "handoff_requested", "request_id": "abc123",
    "service_degraded": False, "offer_support": True, "sources": [],
    "needs_clarification": False, "clarification_options": [],
    "suggested_replies": [], "product_options": [],
    "more_context": {"kind": "support"},
}

SCENARIO = {"id": "99_test", "persona": "test", "scoped": False,
            "turns": [{"q": "I want to talk to a person", "note": "",
                       "live": {"expect": "handoff"}}]}


def _via_testclient(client, seen):
    def fake_post(url, json=None, headers=None, timeout=None):
        seen.append({"url": url, "headers": dict(headers or {}), "json": json})
        path = url.split("127.0.0.1:8000", 1)[-1]
        return client.post(path, json=json, headers=headers or {})
    return fake_post


def test_the_minted_token_is_a_member_the_backend_accepts():
    claims = quota.verify_token(run_live.member_token("live-99-abcd"))
    assert claims and claims["tier"] == "member" and claims["uid"] == "live-99-abcd"


def test_widget_path_posts_to_the_widget_as_a_member_and_names_dropped_keys():
    main.APP_STATE["ready"] = True
    client = TestClient(main.app)
    seen = []
    with patch.object(main, "query_any_language", return_value=dict(QUERY_RESULT)) as q, \
         patch.object(run_live.requests, "post", side_effect=_via_testclient(client, seen)):
        sid, rows = run_live.run_scenario(SCENARIO, "http://127.0.0.1:8000",
                                          verbose=False, path="widget")
    assert seen[0]["url"].endswith("/widget/ask")
    assert seen[0]["headers"]["Authorization"].startswith("Bearer ")
    assert seen[0]["json"]["session_id"] == sid and sid.startswith("live-")
    assert q.called, "a member turn must reach the full pipeline, not the FAQ-only gate"
    row = rows[0]
    assert row["response"].get("role") != "error", row["response"]
    # Today's drops (8.1 adds role/reason/request_id/service_degraded; K03
    # product_options). When 8.1 lands this set must shrink -- update it
    # then, it is the instrument's reading, not a target.
    assert set(row["missing_keys"]) == {"role", "reason", "request_id",
                                        "service_degraded", "product_options"}
    # And the dropped role is visible as a wrong shape: the customer saw a
    # refusal where /query decided a handoff.
    assert row["got"] != "handoff"


def test_agreement_reports_a_disagreement_beside_its_dropped_keys():
    q_rows = [{"turn": SCENARIO["turns"][0], "got": "handoff"}]
    w_rows = [{"turn": SCENARIO["turns"][0], "got": "refuse",
               "missing_keys": ["role"]}]
    agreed, total, diffs = run_live.agreement(
        [(SCENARIO, "s1", q_rows)], [(SCENARIO, "s2", w_rows)])
    assert (agreed, total) == (0, 1)
    assert diffs[0]["query"] == "handoff" and diffs[0]["widget"] == "refuse"
    assert diffs[0]["missing_keys"] == ["role"]


def test_the_query_path_is_unchanged():
    """The default path still posts to /query with no token."""
    seen = []

    class R:
        status_code = 200

        def json(self):
            return dict(QUERY_RESULT, resolved_query="I want to talk to a person")

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.append({"url": url, "headers": headers})
        return R()

    with patch.object(run_live.requests, "post", side_effect=fake_post):
        _, rows = run_live.run_scenario(SCENARIO, "http://x", verbose=False)
    assert seen[0]["url"] == "http://x/query" and not seen[0]["headers"]
    assert rows[0]["got"] == "handoff" and rows[0]["missing_keys"] == []
