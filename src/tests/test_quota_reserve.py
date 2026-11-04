"""D-20261103-03: credits are reserved at check time and settled after the
answer, so a burst of simultaneous requests cannot all pass the same check."""
import itertools
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import _harness  # noqa: F401 -- stubs and the test token secret first
import main
import quota
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_live  # noqa: E402

client = TestClient(main.app)
ANSWER = {"answer": "Hold the reset button for 5 seconds.", "role": "accurate",
          "offer_support": False, "sources": []}
_uid = itertools.count()
M = {"tier": "member", "identity": "u:test", "ip_identity": None}


def _pol(**kw):
    return patch.object(quota, "_policy", lambda k, f: kw.get(k, f))


def _fresh_db():
    quota.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="quota-res-"), "q.db")
    quota.init_db()


def _post(uid, expect=200):
    main.APP_STATE["ready"] = True
    r = client.post("/widget/ask", json={"q": "How do I reset the unit?", "session_id": "s1",
                                         "product": "nv9", "effort": "standard"},
                    headers={"Authorization": "Bearer " + run_live.member_token(uid)})
    assert r.status_code == expect, r.text
    return r.json()


def _uid_new():
    return f"reserve-{next(_uid)}"


def _answering(reply):
    return lambda req, uid, surface: dict(reply)


def test_a_burst_of_checks_cannot_overshoot():
    saved = quota.DB_PATH
    try:
        _fresh_db()
        with _pol(member_daily_credits=3):
            gates = [quota.check(M, 1, reserve=True) for _ in range(10)]
            assert sum(g["allowed"] for g in gates) == 3
            assert all(g["reason"] == "quota" for g in gates if not g["allowed"])
            assert quota.status(M)["used"] == 3
    finally:
        quota.DB_PATH = saved


def test_read_only_check_reserves_nothing():
    saved = quota.DB_PATH
    try:
        _fresh_db()
        with _pol(member_daily_credits=3):
            assert all(quota.check(M, 1)["allowed"] for _ in range(10))
            assert quota.status(M)["used"] == 0
    finally:
        quota.DB_PATH = saved


def test_settle_refunds_the_difference():
    saved = quota.DB_PATH
    try:
        _fresh_db()
        with _pol(member_daily_credits=3):
            g = quota.check(M, 1, reserve=True)
            quota.consume(M, -1, win=g["window"])
            assert quota.status(M)["used"] == 0
            g = quota.check(M, 1, reserve=True)
            quota.consume(M, 0, win=g["window"])
            assert quota.status(M)["used"] == 1
    finally:
        quota.DB_PATH = saved


def test_global_cap_is_reserved_too():
    saved, saved_cap = quota.DB_PATH, quota.global_llm_limit
    try:
        _fresh_db()
        quota.global_llm_limit = lambda: 2
        mk = lambda n: {"tier": "member", "identity": f"u:g{n}", "ip_identity": None}
        assert quota.check(mk(1), 1, reserve=True)["allowed"]
        assert quota.check(mk(2), 1, reserve=True)["allowed"]
        assert quota.check(mk(3), 1, reserve=True)["reason"] == "global_quota"
    finally:
        quota.DB_PATH, quota.global_llm_limit = saved, saved_cap


def test_our_failure_charges_nothing():
    uid = _uid_new()

    def boom(req, uid, surface):
        raise RuntimeError("provider down")
    with patch.object(main, "_traced_query", boom):
        _post(uid, expect=503)
    with patch.object(main, "_traced_query", _answering(ANSWER)):
        a = _post(uid)
    assert a["quota"]["remaining"] == a["quota"]["limit"] - quota.EFFORT["standard"]["credits"]


def test_faq_and_degraded_answers_are_refunded():
    uid = _uid_new()
    with patch.object(main, "_traced_query",
                      _answering({"answer": "x", "service_degraded": True})):
        a = _post(uid)
    assert a["quota"]["remaining"] == a["quota"]["limit"]


def test_credits_are_held_while_answering():
    uid = _uid_new()
    seen = []

    def fake(req, u, surface):
        caller = quota.identify(run_live.member_token(uid), None, "")
        seen.append(quota.check(caller, 1)["allowed"])
        return dict(ANSWER)
    with _pol(member_daily_credits=1), patch.object(main, "_traced_query", fake):
        a = _post(uid)
    assert seen == [False]
    assert a["quota"]["remaining"] == 0


def test_one_at_a_time_is_charged_as_before():
    uid = _uid_new()
    with patch.object(main, "_traced_query", _answering(ANSWER)):
        rem = [_post(uid)["quota"]["remaining"] for _ in range(3)]
    c = quota.EFFORT["standard"]["credits"]
    assert rem[0] - rem[1] == c and rem[1] - rem[2] == c
