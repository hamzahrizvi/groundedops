"""D-20261030-09: "Search more thoroughly" gets the full passages, is refunded
when it finds nothing new, and runs once per question per chat."""
import inspect
import itertools
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

client = TestClient(main.app)
REFUSAL = {"answer": "I could not find that in the documentation.", "role": "rejected",
           "offer_support": True, "sources": []}
ANSWER = {"answer": "Hold the reset button for 5 seconds.", "role": "accurate",
          "offer_support": False, "sources": []}
_uid = itertools.count()


def _run(replies):
    """Fake the pipeline: returns replies in order, records each request."""
    seen, it = [], iter(replies)

    def fake(req, uid, surface):
        seen.append(req)
        return dict(next(it))
    return seen, fake


def _post(uid, effort, sid="s1", q="How do I reset the unit?"):
    main.APP_STATE["ready"] = True
    r = client.post("/widget/ask", json={"q": q, "session_id": sid, "product": "nv9",
                                         "effort": effort},
                    headers={"Authorization": "Bearer " + run_live.member_token(uid)})
    assert r.status_code == 200, r.text
    return r.json()


def _uid_new():
    return f"deep-once-{next(_uid)}"


def test_deep_that_finds_more_is_charged_and_answered():
    uid = _uid_new()
    seen, fake = _run([REFUSAL, ANSWER])
    one_free = {"free_deep_daily": 1}  # so a slot not handed back would show
    with patch.object(quota, "_policy", lambda k, f: one_free.get(k, f)), \
         patch.object(main, "_traced_query", fake):
        s = _post(uid, "standard")
        d = _post(uid, "deep")
    assert d["answer"] == ANSWER["answer"]
    assert s["quota"]["remaining"] - d["quota"]["remaining"] == quota.EFFORT["deep"]["credits"]
    assert d["deep_done"] is True
    assert seen[1].top_k == 16
    assert d["quota"]["deep_available"] is True  # the paid run handed its free slot back


def test_deep_that_finds_nothing_more_is_free():
    uid = _uid_new()
    seen, fake = _run([REFUSAL, {**REFUSAL, "answer": "Sorry, nothing about that is in the manuals."}])
    with patch.object(main, "_traced_query", fake):
        s = _post(uid, "standard")
        d = _post(uid, "deep")
    assert d["answer"] == widget_api.NO_MORE_DETAILS
    assert d["quota"]["remaining"] == s["quota"]["remaining"]
    assert d["deep_done"] is True


def test_a_second_deep_press_is_refused_server_side():
    uid = _uid_new()
    seen, fake = _run([REFUSAL, ANSWER, ANSWER, ANSWER])
    with patch.object(main, "_traced_query", fake):
        _post(uid, "standard")
        d1 = _post(uid, "deep")
        d2 = _post(uid, "deep")
        assert d2["answer"] == widget_api.NO_MORE_DETAILS
        assert d2["quota"]["remaining"] == d1["quota"]["remaining"]
        assert len(seen) == 2, "the pipeline did not run again"
        assert _post(uid, "standard")["deep_done"] is True
        n = len(seen)
        d3 = _post(uid, "deep", sid="other-chat")
        assert d3["answer"] == ANSWER["answer"] and len(seen) == n + 1


def test_a_failed_deep_search_is_given_back():
    uid = _uid_new()
    seen, fake = _run([REFUSAL, {"answer": "", "service_degraded": True}, ANSWER])
    with patch.object(main, "_traced_query", fake):
        s = _post(uid, "standard")
        d = _post(uid, "deep")
        assert d["quota"]["remaining"] == s["quota"]["remaining"]
        assert d["deep_done"] is False
        assert _post(uid, "deep")["answer"] == ANSWER["answer"]
    assert len(seen) == 3


def test_adds_detail_is_strict():
    std = {"answer": "Hold the reset button for 5 seconds.", "answered": True}
    add = widget_api._adds_detail
    assert not add(std, {"answer": "Hold the reset button for five seconds."})
    assert add(std, {"answer": "Hold the reset button for 10 seconds."})
    assert add({"answer": "Do this.", "answered": True},
               {"answer": "Do this.\n1. Open it\n2. Close it"})
    assert not add(std, REFUSAL)
    assert add(None, ANSWER)


def test_effort_widens_the_prompt():
    assert "results[:_ctx_k]" in inspect.getsource(main.query)


def _post_raw(uid, effort, q, sid="cap-chat"):
    main.APP_STATE["ready"] = True
    return client.post("/widget/ask", json={"q": q, "session_id": sid, "product": "nv9",
                                            "effort": effort},
                       headers={"Authorization": "Bearer " + run_live.member_token(uid)})


def test_free_deep_runs_are_capped_and_counted():
    uid, sid = _uid_new(), "cap-chat-" + _uid_new()
    pol = {"member_daily_credits": 4, "questions_per_session": 50}
    seen, fake = _run([REFUSAL] * 10)
    with patch.object(quota, "_policy", lambda k, f: pol.get(k, f)), \
         patch.object(main, "_traced_query", fake):
        cap = quota.free_deep_limit()
        for i in range(cap):
            r = _post_raw(uid, "deep", f"off-topic question number {i}?", sid)
            assert r.status_code == 200, r.text
            assert r.json()["answer"] == widget_api.NO_MORE_DETAILS
            assert r.json()["quota"]["remaining"] == 4
        assert r.json()["quota"]["deep_available"] is False
        over = _post_raw(uid, "deep", "one more off-topic question?", sid)
        assert over.status_code == 429
        assert over.json()["detail"]["reason"] == "deep_daily"
        assert len(seen) == cap, "the capped run never reached the pipeline"
        assert quota.session_check(sid)["questions_used"] == cap
        # the cap stops only deep: a normal question in the same chat still runs
        assert _post_raw(uid, "standard", "still works?", sid).status_code == 200
