"""The widget's contact forms end to end: config the widget actually reads,
enquiry drafting, and lead submission carrying the written-up enquiry.

Focus is on the boundaries that would fail quietly:
  - the destination ADDRESS must never reach a public caller, only the fact
    that one exists,
  - a form with the summary option off must not store a summary even if the
    caller sends one,
  - drafting must not reach a model for an anonymous caller unless that has
    been deliberately enabled.
"""
import _harness
from fastapi.testclient import TestClient

import widget_config

app = _harness.app
client = TestClient(app)
ADMIN = {"x-admin-password": _harness.SUPPORT_TOKEN}

ok = lambda label: print(f"  PASS  {label}")
fails = []


def check(cond, label):
    if cond:
        ok(label)
    else:
        fails.append(label)
        print(f"  FAIL  {label}")


def set_form(kind, **over):
    """Save a whole config with one form overridden, through the real admin
    route, so validation runs the same way it does in the console."""
    cfg = widget_config.get()
    form = dict(cfg[f"{kind}_form"])
    form.update(over)
    payload = {
        "name": cfg["name"], "welcome": cfg["welcome"], "color": cfg["color"],
        "icon_url": cfg.get("icon_url", ""),
        "intro_options": cfg["intro_options"],
        "sales_form": form if kind == "sales" else cfg["sales_form"],
        "support_form": form if kind == "support" else cfg["support_form"],
    }
    r = client.put("/admin/widget/config", headers=ADMIN, json=payload)
    assert r.status_code == 200, r.text
    return r


print("\n== the config the widget reads ==")
r = client.get("/widget/config")
check(r.status_code == 200, f"/widget/config is public ({r.status_code})")
cfg = r.json()
check(all(k in cfg for k in ("name", "welcome", "color", "intro_options",
                             "sales_form", "support_form")),
      "it carries the branding, opening options and both forms")
check("fields" in cfg["support_form"] and cfg["support_form"]["fields"],
      "the support form arrives with its fields, so the widget can build it")

set_form("support", notify_email="support@example.com")
pub = client.get("/widget/config").json()
check(pub["support_form"].get("routed") is True,
      "a configured destination shows as routed:true")
check("support@example.com" not in str(pub),
      "the destination ADDRESS is never published to a public caller")
admin_cfg = client.get("/admin/widget/config", headers=ADMIN).json()
check("support@example.com" in str(admin_cfg),
      "the console still sees the address it configured")

set_form("support", notify_email="")
check(client.get("/widget/config").json()["support_form"]["routed"] is False,
      "no destination shows as routed:false")

print("\n== changes in the console reach the widget's config ==")
set_form("sales", title="Speak to our team")
check(client.get("/widget/config").json()["sales_form"]["title"] == "Speak to our team",
      "a form title saved in the console is what the widget is served")

print("\n== drafting ==")
r = client.post("/widget/draft_enquiry", json={
    "kind": "support", "source": "written",
    "notes": "My MyCheckr Mini will not power on after a firmware update."})
check(r.status_code == 200, f"drafting is reachable publicly ({r.status_code})")
d = r.json()
check("draft" in d and d["draft"].strip(), "it returns a non-empty draft")
check(d["written_by"] == "assembled",
      "an anonymous caller gets the assembled body, not a model call "
      f"(got {d['written_by']!r})")
check("firmware update" in d["draft"],
      "the visitor's own words survive into the draft")

r = client.post("/widget/draft_enquiry", json={
    "kind": "support", "source": "chat",
    "transcript": [{"role": "user", "text": "coin jams on the NV9"},
                   {"role": "bot", "text": "Try cleaning the belt."}]})
check(r.status_code == 200 and "coin jams on the NV9" in r.json()["draft"],
      "drafting from the chat includes what the visitor said")

r = client.post("/widget/draft_enquiry", json={"kind": "nonsense"})
check(r.status_code == 400, f"an unknown kind is refused ({r.status_code})")

print("\n== submitting a lead ==")
set_form("support", allow_summary=True, notify_email="support@example.com")
fields = client.get("/widget/config").json()["support_form"]["fields"]
required = {f["id"]: "x@example.com" if f["type"] == "email" else "Test value"
            for f in fields if f["required"]}

r = client.post("/widget/lead", json={
    "kind": "support", "values": required,
    "enquiry": "Their unit will not power on after a firmware update.",
    "summary_source": "chat",
    "transcript": [{"role": "user", "text": "will not power on"}]})
check(r.status_code == 200, f"a valid submission is accepted ({r.status_code})")
check("values" not in r.json(),
      "the stored lead is not echoed back to a public caller")

leads = client.get("/admin/leads", headers=ADMIN).json()["leads"]
latest = leads[0] if leads else {}
check(latest.get("enquiry", "").startswith("Their unit will not power on"),
      "the enquiry body is stored and visible to the console")
check(latest.get("summary_source") == "chat",
      "how the summary was produced is recorded, so staff know a model wrote it")
check(latest.get("notify_email") == "support@example.com",
      "the destination is recorded on the lead")
check(latest.get("notified") is False,
      "and it is honestly marked as not emailed — there is no mail server yet")

r = client.post("/widget/lead", json={"kind": "support", "values": {}})
check(r.status_code == 400, f"a submission missing required fields is refused ({r.status_code})")

print("\n== a form with summaries off must not store one ==")
set_form("support", allow_summary=False)
r = client.post("/widget/lead", json={
    "kind": "support", "values": required,
    "enquiry": "should not be stored", "summary_source": "written",
    "transcript": [{"role": "user", "text": "should not be stored either"}]})
check(r.status_code == 200, "the submission itself still works")
latest = client.get("/admin/leads", headers=ADMIN).json()["leads"][0]
check(latest.get("enquiry") == "",
      "the enquiry is dropped, because the admin turned summaries off")
check(latest.get("summary_source") == "none", "and the source is reset to none")
check(latest.get("transcript") == [],
      "the transcript is dropped for the same reason")

print("\n== the preview page the console iframes ==")
r = client.get("/widget/preview")
check(r.status_code == 200, f"/widget/preview is served ({r.status_code})")
check("groundedops-widget.js" in r.text,
      "it embeds the real widget script rather than reimplementing a chat")
check("noindex" in r.text, "and asks not to be indexed")

print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
