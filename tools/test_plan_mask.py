"""Self-check for masking by `- Sensitive: yes` (D-20261020-07): ops_ledger cards, the public plan block and the rejected list.

  python tools/lab_lock.py python tools/test_plan_mask.py

A decision section or inbox note that says `- Sensitive: yes` is masked whoever found it; `no` and no line are not.
"""
import datetime
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ops_ledger import ledger  # noqa: E402
from pending_plan import block, rejected_lines  # noqa: E402


def sec(did, title, frm, verdict, extra=""):
    return f"## {did} | {title}\n- From: {frm}\n- Assigned: dev\n- Verdict: {verdict}\n{extra}\n"


def main():
    with tempfile.TemporaryDirectory() as ops:
        for d in ("decisions", "inbox/rnd", "inbox/dev"):
            os.makedirs(os.path.join(ops, d))
        w = lambda p, s: open(os.path.join(ops, p), "w", encoding="utf-8").write(s)
        w("decisions/20261020.md",
          sec("D-20261020-01", "A confidential guide answers customers", "rnd", "AUTO-APPROVED", "- Sensitive: yes")
          + sec("D-20261020-02", "Logs keep customer emails", "dev", "REJECT",
                "- For the rejected list: Logs keep emails: accepted.\n- Sensitive: YES")
          + sec("D-20261020-03", "Comparisons use the wrong manual", "rnd", "AUTO-APPROVED")
          + sec("D-20261020-04", "Procedures get setup steps", "customer", "REJECT",
                "- For the rejected list: Procedures: later.\n- Sensitive: no")
          + sec("D-20261020-05", "Later triage marks it", "rnd", "AUTO-APPROVED"))
        w("decisions/20261020b.md", "## D-20261020-05 | Later triage marks it\n- Verdict: AUTO-APPROVED\n- Sensitive: yes\n")
        w("inbox/rnd/20261020-leak.md", "# The widget quotes a private file\n\n- Sensitive: yes\n")
        w("inbox/rnd/20261020-speed.md", "# Answers are slow\n")
        today = datetime.date(2026, 10, 21)
        cards = ledger(ops, today=today)["cards"]
        got = {c["id"]: c["security"] for c in cards}
        want = {"D-20261020-01": True, "D-20261020-02": True, "D-20261020-03": False, "D-20261020-04": False,
                "D-20261020-05": True, "rnd/20261020-leak": True, "rnd/20261020-speed": False}
        assert got == want, got
        t = block(cards, today)
        assert "| D-20261020-01 | Security item D-20261020-01, details in the office |" in t, t
        assert "| D-20261020-03 | Comparisons use the wrong manual |" in t, t
        assert "| rnd/20261020-speed | Answers are slow |" in t, t
        assert "| rnd/20261020 | Security item rnd/20261020, details in the office |" in t, t
        for leak in ("confidential", "customer emails", "private file", "Later triage", "-leak"):
            assert leak not in t, leak
        assert [d for d, _ in rejected_lines(ops)] == ["D-20261020-04"], rejected_lines(ops)
    print("selftest ok")


if __name__ == "__main__":
    main()
