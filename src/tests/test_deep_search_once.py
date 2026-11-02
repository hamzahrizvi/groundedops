"""D-20261030-09: "Search more thoroughly" gets the full passages, is refunded
when it finds nothing new, and runs once per question per chat."""
import inspect
import itertools
import sys
from pathlib import Path
from unittest.mock import patch

import _harness  # noqa: F401 -- stubs and the test token secret first
import main
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
    with patch.object(main, "_traced_query", fake):
        _post(uid, "standard")
        d = _post(uid, "deep")
    assert d["answer"] == ANSWER["answer"]
    assert d["deep_done"] is True
    assert seen[1].top_k == 16


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
