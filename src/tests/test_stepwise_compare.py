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


compared, recorded = [], []


def _fake_compare(q, parts):
    compared.append(parts)
    return "The BV30 draws less."


def _events(steps, compare=_fake_compare):
    asked.clear(), compared.clear(), recorded.clear()
    sid = "live-step-1"
    main.APP_STATE["ready"] = True
    with patch.object(main, "query_any_language", _fake_pipeline), \
         patch.object(main, "log_interaction", lambda *a, **k: None), \
         patch.object(stepwise, "plan", lambda q: steps), \
         patch.object(stepwise, "compare", compare), \
         patch.object(stepwise, "record", lambda *a: recorded.append(a)):
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
    # The comparison is written from the two parts, not a third pipeline run.
    text = "".join(d["text"] for k, d in ev if k == "delta")
    assert text == "### Comparison\n\nThe BV30 draws less."
    assert compared == [[("NV9USB+", parts[0]["answer"]), ("BV30", parts[1]["answer"])]]
    meta = next(d for k, d in ev if k == "meta")
    assert meta["role"] == "comparison" and meta["request_id"] and meta["sources"] == []
    # Remembered as the visitor's own turn, in the visitor's own chat.
    assert recorded[0][:3] == ("live-step-1", "NV9USB+ vs BV30 supply?", "The BV30 draws less.")
    # Each part asked its own question, scoped to its product, in its own
    # chat, past the FAQ.
    assert [a[:2] for a in asked] == [(s[2], s[0]) for s in STEPS]
    assert {a[2] for a in asked}.isdisjoint({"live-step-1"})
    assert [a[3] for a in asked] == [True, True]


def test_no_comparison_written_falls_back_to_the_pipeline():
    ev = _events(STEPS, compare=lambda q, parts: None)
    assert len([d for k, d in ev if k == "part"]) == 2
    text = "".join(d["text"] for k, d in ev if k == "delta")
    assert text == "### Comparison\n\nAnswer to: NV9USB+ vs BV30 supply?"
    assert asked[2][2] == "live-step-1" and asked[2][3] is False


def test_a_question_back_is_not_shown_as_a_part():
    ev = _events([STEPS[0], ("bv30", "BV30", "CLARIFY the BV30 supply?")])
    assert [d["title"] for k, d in ev if k == "part"] == ["NV9USB+"]
    # One half missing: the pipeline compares, nothing is written from parts.
    assert not compared and asked[-1][0] == "NV9USB+ vs BV30 supply?"


def test_no_plan_is_one_plain_answer():
    ev = _events([])
    assert not [d for k, d in ev if k == "part"]
    text = "".join(d["text"] for k, d in ev if k == "delta")
    assert text == "Answer to: NV9USB+ vs BV30 supply?"
