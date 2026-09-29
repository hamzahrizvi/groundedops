"""Access policy: guest AI access, daily allowances, per-session caps.

The valuable assertions here are the ones about the DEFAULT: guests must get
no model access until someone deliberately turns it on, and turning it off
again must actually close it. A regression there is a bill, not a bug report.

_harness.py points POLICY_PATH at a scratch file, so nothing here can change
the real install's limits.
"""
import _harness
from fastapi.testclient import TestClient

import policy
import quota

app = _harness.app
client = TestClient(app)

ROOT = {"x-admin-password": _harness.ROOT_TOKEN}
SUPPORT = {"x-admin-password": _harness.SUPPORT_TOKEN}
BASIC = {"x-admin-password": _harness.BASIC_TOKEN}

ok = lambda label: print(f"  PASS  {label}")
fails = []


def check(cond, label):
    if cond:
        ok(label)
    else:
        fails.append(label)
        print(f"  FAIL  {label}")


print("\n== defaults ==")
policy.reset()
check(policy.value("anon_llm_enabled") is False,
      "guest AI access is OFF by default")
check(quota.limit_for("anonymous") == 0,
      "so a guest has zero model credits")
check(quota.limit_for("member") > 0, "a member has some")
check(policy.value("questions_per_session") == 0,
      "per-session caps are unlimited by default")

print("\n== level boundaries ==")
check(client.get("/admin/policy", headers=ROOT).status_code == 200,
      "root reaches the policy page")
check(client.get("/admin/policy", headers=SUPPORT).status_code == 403,
      "support cannot see limits (they have a cost consequence)")
check(client.get("/admin/policy", headers=BASIC).status_code == 403,
      "basic cannot see limits")
r = client.put("/admin/policy", headers=SUPPORT,
               json={"changes": {"member_daily_credits": 9999}})
check(r.status_code == 403, f"support cannot change a limit ({r.status_code})")
check(policy.value("member_daily_credits") != 9999,
      "and the refused attempt changed nothing")

print("\n== validation ==")
r = client.put("/admin/policy", headers=ROOT, json={"changes": {"nonsense": 1}})
check(r.status_code == 400,
      f"an unknown setting is refused, not silently dropped ({r.status_code})")
r = client.put("/admin/policy", headers=ROOT,
               json={"changes": {"member_daily_credits": -5}})
check(r.status_code == 400, f"a negative allowance is refused ({r.status_code})")
r = client.put("/admin/policy", headers=ROOT,
               json={"changes": {"member_daily_credits": "abc"}})
check(r.status_code == 400, f"a non-numeric allowance is refused ({r.status_code})")
r = client.put("/admin/policy", headers=ROOT,
               json={"changes": {"member_daily_credits": 99999999}})
check(r.status_code == 400,
      f"an absurd allowance is capped rather than accepted ({r.status_code})")

print("\n== guest AI access, on and off ==")
r = client.put("/admin/policy", headers=ROOT, json={"changes": {
    "anon_llm_enabled": True, "anon_llm_credits": 3}})
check(r.status_code == 200, "root can open AI to guests")
check(quota.anon_llm_enabled() is True, "and quota sees it immediately")
check(quota.limit_for("anonymous") == 3,
      "a guest now has the configured allowance")
level, spec = quota.resolve_effort("standard", "anonymous")
check(level == "standard",
      f"a guest may now reach the generative pipeline (got {level!r})")

client.put("/admin/policy", headers=ROOT,
           json={"changes": {"anon_llm_enabled": False}})
check(quota.limit_for("anonymous") == 0, "switching it off closes it again")
level, spec = quota.resolve_effort("standard", "anonymous")
check(level == "faq_only",
      f"and a guest is back to curated answers only (got {level!r})")

print("\n== the notice guests are shown ==")
client.put("/admin/policy", headers=ROOT,
           json={"changes": {"anon_notice": "Accounts only, sorry."}})
st = quota.status({"tier": "anonymous", "identity": "vis-1", "uid": None})
check(st["account_notice"] == "Accounts only, sorry.",
      "the configured wording reaches the widget's quota payload")
check(st["ai_available"] is False, "and it is told AI is unavailable")

client.put("/admin/policy", headers=ROOT,
           json={"changes": {"anon_llm_enabled": True}})
st = quota.status({"tier": "anonymous", "identity": "vis-1", "uid": None})
check(st["ai_available"] is True,
      "with guest AI on, the widget is told AI IS available")
client.put("/admin/policy", headers=ROOT,
           json={"changes": {"anon_llm_enabled": False}})

print("\n== per-session caps ==")
policy.update({"questions_per_session": 2, "tokens_per_session": 0})
s1 = "sess-alpha"
check(quota.session_check(s1)["allowed"] is True, "first question fits")
quota.session_record(s1)
check(quota.session_check(s1)["allowed"] is True, "second question fits")
quota.session_record(s1)
res = quota.session_check(s1)
check(res["allowed"] is False and res["reason"] == "session_questions",
      f"third is refused with a session-specific reason ({res['reason']})")
check(quota.session_check("sess-beta")["allowed"] is True,
      "a different conversation is unaffected — the cap is per session")

policy.update({"questions_per_session": 0, "tokens_per_session": 100})
s2 = "sess-tokens"
check(quota.session_check(s2, tokens_wanted=50)["allowed"] is True,
      "a request inside the token budget is allowed")
quota.session_record(s2, tokens_used=90)
res = quota.session_check(s2, tokens_wanted=50)
check(res["allowed"] is False and res["reason"] == "session_tokens",
      f"one that would exceed it is refused ({res['reason']})")

policy.update({"questions_per_session": 0, "tokens_per_session": 0})
check(quota.session_check(s1)["allowed"] is True,
      "setting both caps to 0 lifts them again")

print("\n== reset ==")
policy.update({"member_daily_credits": 7})
r = client.post("/admin/policy/reset", headers=ROOT)
check(r.status_code == 200 and policy.value("member_daily_credits") != 7,
      "reset restores the installed defaults")

print("\n== signed-in preview token ==")
r = client.post("/admin/widget/preview_token", headers=SUPPORT)
check(r.status_code == 200 and r.json().get("token"),
      f"support can mint a preview token ({r.status_code})")
check(r.json()["tier"] == "member",
      "it is a member token — previewing as staff would show wrong allowances")
check(r.json()["expires_in"] <= 1800, "and it is short-lived")
check(client.post("/admin/widget/preview_token", headers=BASIC).status_code == 403,
      "basic cannot mint one")
tok = r.json()["token"]
check(quota.verify_token(tok) is not None, "the minted token actually verifies")

print("\n== site-wide daily cap (9.4) ==")
import os
os.environ.pop("WIDGET_AI_DRAFT_ANONYMOUS", None)
policy.update({"anon_llm_enabled": True, "anon_llm_credits": 3,
               "global_llm_daily": 2})
quota.consume(quota.identify(None, "g1", "203.0.113.1"), 1)
quota.consume(quota.identify(None, "g2", "203.0.113.2"), 1)
res = quota.check(quota.identify(None, "g3", "203.0.113.3"), 1)
check(res["reason"] == "global_quota",
      f"a fresh guest on a fresh IP hits the site-wide cap ({res['reason']})")
member = quota.identify(quota.issue_token("m1", "member"), None, "x")
check(quota.check(member, 1)["reason"] == "global_quota", "so does a member")
staff = quota.identify(quota.issue_token("s1", "staff"), None, "x")
check(quota.check(staff, 1)["allowed"], "staff are exempt")
quota.consume(staff, 5)
with quota._conn() as c:
    spent = quota._used(c, quota.GLOBAL_IDENTITY, quota._window_start())
check(spent == 2, f"and do not spend the site's budget ({spent})")

import logger as _logger
import pipeline_trace
from unittest.mock import patch
rows = []
with patch.object(_logger, "log_interaction", side_effect=lambda *a, **k:
                  rows.append(pipeline_trace.snapshot())):
    r = client.post("/widget/ask", json={"q": "How do I clear a coin jam?",
                                         "visitor_id": "g4",
                                         "session_id": "s-guest"})
check(r.status_code == 200 and r.json().get("effort") == "faq_only",
      f"a capped guest gets the FAQ path, not a 429 ({r.status_code})")
check(len(rows) == 1 and rows[0]["meta"]["origin"] == "widget"
      and rows[0]["meta"]["session_id"] == "s-guest"
      and rows[0]["exit"]["id"] in pipeline_trace.OUTCOMES,
      f"and the guest turn is logged with M2's fields ({rows})")
r = client.post("/widget/ask", json={"q": "How do I clear a coin jam?"},
                headers={"authorization": "Bearer "
                         + quota.issue_token("m2", "member")})
check(r.status_code == 429
      and r.json()["detail"]["reason"] == "global_quota",
      f"a capped member gets the 429 with its own reason ({r.status_code})")

policy.update({"global_llm_daily": 0})
from unittest.mock import patch
import main
with patch.object(main, "generate_with_fallback",
                  return_value={"text": "Enquiry: NV9 has no power."}):
    r = client.post("/widget/draft_enquiry", json={
        "kind": "support", "source": "written",
        "notes": "NV9 will not power on."})
check(r.status_code == 200 and r.json()["written_by"] != "assembled",
      "the console switch alone opens drafting to guests "
      f"(got {r.json().get('written_by')!r})")
policy.update({"anon_llm_enabled": False})

print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
