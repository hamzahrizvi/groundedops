"""8.8: the capability offer -> "yes" -> steps loop, through /widget/ask.

The 2026-09-22 failure was a human on the widget: offer made, "yes" x4
refused. test_steps_offer.py pins the pieces; nothing drove the "yes"
through the customer's surface until this. The steps lookup is stubbed:
_harness stubs retrieval_db without steps_for_source, and _steps_answer
swallows that ImportError into "no steps", which would pass vacuously.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import _harness  # noqa: F401 -- stubs and the test token secret first
import main
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_live  # noqa: E402

SRC = "Accessing my device in Linux Environment-v2-20250224_144232 2.pdf"
STEPS = "**Step 1: Create a new udev rule file**\nsudo touch /etc/udev/rules.d/80-local.rules"


def _ask(q, sid):
    main.APP_STATE["ready"] = True
    client = TestClient(main.app)
    with patch.object(main, "_steps_answer", return_value=STEPS), \
         patch.object(main, "log_interaction", lambda *a, **k: None), \
         patch.object(main, "add_to_memory", lambda *a, **k: None):
        r = client.post("/widget/ask", json={"q": q, "session_id": sid},
                        headers={"Authorization": "Bearer " + run_live.member_token(sid)})
    assert r.status_code == 200, r.text
    return r.json()


def test_yes_on_the_widget_serves_the_offered_steps():
    sid = "live-88-yes"
    main._remember_steps_offer(sid, SRC)
    r = _ask("Yes, show me the steps", sid)
    assert r["answer"] == STEPS
    assert "Linux Environment" in r["sources"][0]["source"]
    assert sid not in main._PENDING_STEPS, "the offer is spent"


def test_no_thanks_on_the_widget_declines_and_spends_the_offer():
    sid = "live-88-no"
    main._remember_steps_offer(sid, SRC)
    r = _ask("No thanks", sid)
    assert r["answer"].startswith("No problem")
    assert sid not in main._PENDING_STEPS


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
