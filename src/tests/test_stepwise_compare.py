"""v17.4: a two-product comparison streams one part per product, then the
comparison. The split call (stepwise.plan) and the pipeline are faked;
the stream, the gates and the event order are real."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import _harness  # noqa: F401 -- stubs and the test token secret first
import main
import stepwise
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_live  # noqa: E402

client = TestClient(main.app)
STEPS = [("nv9usb", "NV9USB+", "What supply does the NV9USB+ need?"),
         ("bv30", "BV30", "What supply does the BV30 need?")]
asked = []


def _fake_pipeline(payload, x_user_id=None):
    asked.append((payload.q, payload.product, payload.session_id, payload.skip_faq))
    if "CLARIFY" in payload.q:
        return {"answer": "Which BV30 do you have?", "sources": [], "role": "clarify",
                "needs_clarification": True, "request_id": "rc"}
    return {"answer": "Answer to: " + payload.q, "sources": [], "role": "fast",
            "request_id": "r" + str(len(asked))}


def _events(steps):
    asked.clear()
    sid = "live-step-1"
    main.APP_STATE["ready"] = True
    with patch.object(main, "query_any_language", _fake_pipeline), \
         patch.object(main, "log_interaction", lambda *a, **k: None), \
         patch.object(stepwise, "plan", lambda q: steps):
        r = client.post("/widget/ask/stream",
                        json={"q": "NV9USB+ vs BV30 supply?", "session_id": sid},
                        headers={"Authorization": "Bearer " + run_live.member_token(sid)})
    assert r.status_code == 200, r.text
    out = []
    for frame in r.text.split("\n\n"):
        lines = dict(l.split(": ", 1) for l in frame.splitlines() if ": " in l)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_each_product_then_the_comparison():
    ev = _events(STEPS)
    parts = [d for k, d in ev if k == "part"]
    assert [p["title"] for p in parts] == ["NV9USB+", "BV30"]
    assert parts[0]["answer"] == "Answer to: What supply does the NV9USB+ need?"
    stages = [d["stage"] for k, d in ev if k == "status"]
    assert stages.index("Looking up the BV30") < stages.index("Comparing the two")
    kinds = [k for k, _ in ev]
    assert kinds.index("part") < kinds.index("meta")
    text = "".join(d["text"] for k, d in ev if k == "delta")
    assert text == "### Comparison\n\nAnswer to: NV9USB+ vs BV30 supply?"
    # Each part asked its own question, scoped to its product, in its own
    # chat, past the FAQ; the comparison itself is the visitor's own turn.
    assert [a[:2] for a in asked[:2]] == [(s[2], s[0]) for s in STEPS]
    assert asked[0][2] != asked[2][2] and asked[2][2] == "live-step-1"
    assert [a[3] for a in asked] == [True, True, False]


def test_a_question_back_is_not_shown_as_a_part():
    ev = _events([STEPS[0], ("bv30", "BV30", "CLARIFY the BV30 supply?")])
    assert [d["title"] for k, d in ev if k == "part"] == ["NV9USB+"]
    assert "meta" in [k for k, _ in ev]


def test_no_plan_is_one_plain_answer():
    ev = _events([])
    assert not [d for k, d in ev if k == "part"]
    text = "".join(d["text"] for k, d in ev if k == "delta")
    assert text == "Answer to: NV9USB+ vs BV30 supply?"
