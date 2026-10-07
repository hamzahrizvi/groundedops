"""8.9: a handoff gives the visitor a reference, emails the enquiry once mail
is set up, and the public form cannot be flooded or used to wipe enquiries.

mailer.send is faked, as test_credit_alerts does; nothing leaves the box.
"""
from unittest.mock import patch

import _harness
from fastapi.testclient import TestClient

import mailer
import quota
import widget_config

client = TestClient(_harness.app)
ADMIN = {"x-admin-password": _harness.SUPPORT_TOKEN}
fails = []


def check(cond, label):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        fails.append(label)


cfg = widget_config.get()
form = dict(cfg["support_form"], notify_email="support@example.com",
            allow_summary=True)
r = client.put("/admin/widget/config", headers=ADMIN, json={
    "name": cfg["name"], "welcome": cfg["welcome"], "color": cfg["color"],
    "icon_url": cfg.get("icon_url", ""), "intro_options": cfg["intro_options"],
    "sales_form": cfg["sales_form"], "support_form": form})
assert r.status_code == 200, r.text
fields = client.get("/widget/config").json()["support_form"]["fields"]
values = {f["id"]: "visitor@example.org" if f["type"] == "email" else "Test value"
          for f in fields if f["required"]}
_n = [0]


def post(**extra):
    """A lead from a fresh visitor, so the daily cap stays out of the way."""
    _n[0] += 1
    body = {"kind": "support", "values": values, "visitor_id": f"v{_n[0]}"}
    body.update(extra)
    return client.post("/widget/lead", json=body)


def stored(lead_id):
    return next((l for l in widget_config.list_leads(limit=10_000, include_sent=True)
                 if l["id"] == lead_id), None)


print("\n== the reference and the email ==")
sent = []
with patch.object(mailer, "is_configured", lambda: True), \
     patch.object(mailer, "send", lambda to, subject, body, reply_to="":
                  sent.append((to, subject, body, reply_to))):
    r = post(transcript=[{"role": "user", "text": "it will not power on"}])
check(r.status_code == 200, f"a valid lead is accepted ({r.status_code})")
d = r.json()
ref = widget_config.lead_ref(d["id"])
check(d.get("reference") == ref and ref.startswith("GO-") and len(ref) == 11,
      f"the visitor is given a GO- reference ({d.get('reference')})")
check(len(sent) == 1 and sent[0][0] == ["support@example.com"],
      "one email, to the form's destination only")
check(sent and ref in sent[0][1], "the subject carries the reference")
check(sent and "visitor@example.org" not in sent[0][0],
      "the visitor-typed address is never a recipient (no open relay)")
check(sent and "it will not power on" in sent[0][2], "the body carries the chat")
check(sent and sent[0][3] == "visitor@example.org",
      "the visitor's address is the Reply-To, so the team's reply reaches them")
stub = stored(d["id"])
check(stub["notified"] is True and "values" not in stub and "transcript" not in stub
      and "cc_email" not in stub,
      f"an emailed lead is cut to a contact-free stub ({sorted(stub)})")
listed = client.get("/admin/leads", headers=ADMIN).json()["leads"]
check(all(l["id"] != d["id"] for l in listed),
      "and the console no longer lists it")
import log_report
check(any(l["id"] == d["id"] for l in log_report.load_leads()),
      "but the weekly report still counts it")

print("\n== the message headers ==")
import smtplib
got = []
class FakeSMTP:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def ehlo(self): pass
    def starttls(self, **k): pass
    def login(self, *a): pass
    def send_message(self, msg): got.append(msg)
smtp = {"host": "smtp.example.com", "port": 587, "security": "starttls",
        "user": "", "sender": "noreply@example.com"}
with patch.object(mailer.keystore, "get_smtp_settings", lambda: smtp), \
     patch.object(smtplib, "SMTP", FakeSMTP):
    mailer.send(["support@example.com"], "s", "b", reply_to="visitor@example.org")
    mailer.send(["support@example.com"], "s", "b", reply_to="bad\r\nBcc: x@y.z")
check(got[0]["Auto-Submitted"] == "auto-generated",
      "every message is marked automated (no helpdesk auto-reply loop)")
check(got[0]["Reply-To"] == "visitor@example.org", "Reply-To is set")
check(got[1]["Reply-To"] is None and got[1]["Bcc"] is None,
      "a malformed address is dropped, not injected as headers")

print("\n== a failed send keeps the lead ==")
def boom(*a, **k):
    raise mailer.MailError("could not reach smtp.example.com:587")
with patch.object(mailer, "is_configured", lambda: True), \
     patch.object(mailer, "send", boom):
    r = post()
check(r.status_code == 200, f"the visitor still gets a 200 ({r.status_code})")
lead = stored(r.json()["id"])
check(lead is not None and lead["notified"] is False,
      "the lead is stored and marked not emailed")

print("\n== no mail server, no attempt ==")
sent.clear()
with patch.object(mailer, "is_configured", lambda: False), \
     patch.object(mailer, "send", lambda *a: sent.append(a)):
    r = post()
check(r.status_code == 200 and sent == [], "unconfigured -> zero send attempts")
stats = client.get("/admin/leads", headers=ADMIN).json()["stats"]
check(stats.get("unsent", 0) >= 2, f"the console counts unsent leads ({stats.get('unsent')})")

print("\n== the daily cap ==")
per_visitor = quota.PUBLIC_WRITE_LIMITS["lead"][0]
before = len(widget_config.list_leads(limit=10_000))
codes = [client.post("/widget/lead", json={"kind": "support", "values": values,
                                           "visitor_id": "flood"}).status_code
         for _ in range(per_visitor + 1)]
after = len(widget_config.list_leads(limit=10_000))
check(codes[:-1] == [200] * per_visitor and codes[-1] == 429,
      f"lead {per_visitor + 1} from one visitor is refused with 429 ({codes})")
check(after - before == per_visitor, "and the refused one is not stored")
with patch.dict(quota.PUBLIC_WRITE_LIMITS, {"lead": (100, 2)}):
    codes = [client.post("/widget/lead", json={"kind": "support", "values": values,
                                               "visitor_id": f"ip{i}"},
                         headers={"cf-connecting-ip": "203.0.113.50"}).status_code
             for i in range(3)]
check(codes == [200, 200, 429],
      f"new visitor ids from one IP still hit the per-IP cap ({codes})")

print("\n== size limits ==")
r = post(transcript=[{"role": "user", "text": "x" * 50_000}])
t = stored(r.json()["id"])["transcript"][0]["text"]
check(r.status_code == 200 and len(t) == widget_config.MAX_ENQUIRY_CHARS,
      f"a 50 KB transcript entry is stored truncated ({len(t)} chars)")

existing = widget_config._load_leads()
widget_config._save_leads([])
try:
    with patch.object(widget_config, "MAX_LEADS", 2):
        a, b = post().json()["id"], post().json()["id"]
        r = post()
    check(r.status_code == 503, f"a full store refuses with 503 ({r.status_code})")
    ids = [l["id"] for l in widget_config._load_leads()]
    check(ids == [a, b], "and the two stored leads are untouched (no eviction)")
    with patch.object(widget_config, "MAX_LEADS", 2):
        check(widget_config.lead_stats()["full"] is True, "the console is told it is full")
finally:
    widget_config._save_leads(existing)

print("\n== X-User-Id grants nothing ==")
q = client.get("/widget/quota?visitor_id=x", headers={"x-user-id": "admin"}).json()
check(q.get("tier") == "anonymous", f"/widget/quota ignores X-User-Id ({q.get('tier')})")
for path in ("/conversations", "/query"):
    r = client.request("GET" if path == "/conversations" else "POST", path,
                       headers={"x-user-id": "admin", "x-forwarded-for": "203.0.113.7"},
                       json={"question": "hi"})
    check(r.status_code == 404, f"{path} is 404 to a proxied caller ({r.status_code})")

if fails:
    raise SystemExit(f"{len(fails)} check(s) failed")
print("ALL CHECKS PASSED")
