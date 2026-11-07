"""v17.4: a two-product comparison whose normal answer asked "which
product?" streams one part per product, then the comparison; a full normal
answer stands. The split call (stepwise.plan) and the pipeline are faked;
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
Q = "NV9USB+ vs BV30 supply?"
asked, planned = [], []
normal = {}   # what the normal pipeline answers Q with


def _fake_pipeline(payload, x_user_id=None):
    asked.append((payload.q, payload.product, payload.session_id, payload.skip_faq))
    if payload.q == Q and normal:
        return dict(normal, request_id="r0")
    if "CLARIFY" in payload.q:
        return {"answer": "Which BV30 do you have?", "sources": [], "role": "clarify",
                "needs_clarification": True, "request_id": "rc"}
    return {"answer": "Answer to: " + payload.q, "sources": [], "role": "fast",
            "request_id": "r" + str(len(asked))}


compared, recorded = [], []


def _fake_compare(q, parts):
    compared.append(parts)
    return "The BV30 draws less."


WHICH = {"answer": "Which product do you mean?", "sources": [], "role": "clarify",
         "needs_clarification": True}


def _events(steps, compare=_fake_compare, first=WHICH):
    asked.clear(), planned.clear(), compared.clear(), recorded.clear()
    normal.clear(), normal.update(first)
    sid = "live-step-1"
    main.APP_STATE["ready"] = True
    with patch.object(main, "query_any_language", _fake_pipeline), \
         patch.object(main, "log_interaction", lambda *a, **k: None), \
         patch.object(stepwise, "products", lambda q: ["nv9usb", "bv30"]), \
         patch.object(stepwise, "plan", lambda q: planned.append(q) or steps), \
         patch.object(stepwise, "compare", compare), \
         patch.object(stepwise, "record", lambda *a: recorded.append(a)):
        r = client.post("/widget/ask/stream",
                        json={"q": Q, "session_id": sid},
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
    # The comparison is written from the two parts, not a pipeline run.
    text = "".join(d["text"] for k, d in ev if k == "delta")
    assert text == "### Comparison\n\nThe BV30 draws less."
    assert compared == [[("NV9USB+", parts[0]["answer"]), ("BV30", parts[1]["answer"])]]
    meta = next(d for k, d in ev if k == "meta")
    assert meta["role"] == "comparison" and meta["request_id"] and meta["sources"] == []
    # Remembered as the visitor's own turn, in the visitor's own chat.
    assert recorded[0][:3] == ("live-step-1", Q, "The BV30 draws less.")
    # The normal answer first, in the visitor's chat; then each part asked
    # its own question, scoped to its product, in its own chat, past the FAQ.
    assert asked[0] == (Q, None, "live-step-1", False)
    assert [a[:2] for a in asked[1:]] == [(s[2], s[0]) for s in STEPS]
    assert {a[2] for a in asked[1:]}.isdisjoint({"live-step-1"})
    assert [a[3] for a in asked[1:]] == [True, True]


def test_no_comparison_written_keeps_the_normal_answer():
    ev = _events(STEPS, compare=lambda q, parts: None)
    assert len([d for k, d in ev if k == "part"]) == 2
    text = "".join(d["text"] for k, d in ev if k == "delta")
    assert text == "Which product do you mean?"
    assert len(asked) == 3 and not recorded


def test_a_question_back_is_not_shown_as_a_part():
    ev = _events([STEPS[0], ("bv30", "BV30", "CLARIFY the BV30 supply?")])
    assert [d["title"] for k, d in ev if k == "part"] == ["NV9USB+"]
    # One half missing: nothing is written from parts, no pipeline rerun.
    assert not compared and len(asked) == 3


def test_a_full_answer_stands():
    # Measured: against a full answer the steps lost or tied, for 3.7x the
    # tokens; the split is not even planned.
    ev = _events(STEPS, first={"answer": "Both need 12 V.", "sources": [], "role": "fast"})
    assert not planned and not [d for k, d in ev if k == "part"]
    text = "".join(d["text"] for k, d in ev if k == "delta")
    assert text == "Both need 12 V." and len(asked) == 1
