"""End-to-end checks for the v13.0 routes, run against FastAPI's TestClient
with retrieval/generation stubbed. Focus: auth boundaries, validation,
persistence, and the public/admin split.
"""
import json
import _harness
from fastapi.testclient import TestClient

app = _harness.app
main = _harness.main
client = TestClient(app)
ADMIN = {"x-admin-password": "testpw"}
BAD = {"x-admin-password": "wrong"}

ok = lambda label: print(f"  PASS  {label}")
fails = []


def check(cond, label):
    if cond:
        ok(label)
    else:
        fails.append(label)
        print(f"  FAIL  {label}")


print("\n== auth boundaries ==")
# Admin-only routes must reject a bad password.
for method, path in [("get", "/faq/gaps"), ("get", "/admin/widget/config"),
                     ("get", "/admin/leads"), ("post", "/admin/faq/autogenerate")]:
    kw = {"json": {"source": "x"}} if method == "post" else {}
    r = getattr(client, method)(path, headers=BAD, **kw)
    check(r.status_code == 401, f"{method.upper()} {path} rejects bad password ({r.status_code})")

# Public routes must NOT require a password.
r = client.get("/widget/config")
check(r.status_code == 200, f"GET /widget/config is public ({r.status_code})")

print("\n== widget config ==")
cfg = client.get("/admin/widget/config", headers=ADMIN).json()
check("notify_email" in cfg["sales_form"], "admin config exposes notify_email")

pub = client.get("/widget/config").json()
check("notify_email" not in pub["sales_form"], "public config HIDES notify_email")
check("notify_email" not in pub["support_form"], "public config hides support notify_email")

cfg["name"] = "Acme Support"
cfg["color"] = "#22aa44"
cfg["sales_form"]["notify_email"] = "internal@acme.test"
r = client.put("/admin/widget/config", headers=ADMIN, json=cfg)
check(r.status_code == 200 and r.json()["name"] == "Acme Support", "save valid config")

pub = client.get("/widget/config").json()
check(pub["name"] == "Acme Support" and pub["color"] == "#22aa44", "public reflects saved config")
check("internal@acme.test" not in json.dumps(pub), "saved notify_email never leaks publicly")

# validation
bad_cfg = dict(cfg); bad_cfg["color"] = "javascript:alert(1)"
r = client.put("/admin/widget/config", headers=ADMIN, json=bad_cfg)
check(r.status_code == 400, f"rejects non-hex color ({r.status_code})")

bad_cfg = dict(cfg); bad_cfg["intro_options"] = [{"label": "Pwn", "action": "exec"}]
r = client.put("/admin/widget/config", headers=ADMIN, json=bad_cfg)
check(r.status_code == 400, f"rejects unknown intro action ({r.status_code})")

print("\n== leads ==")
fields = {f["id"]: f["label"] for f in pub["sales_form"]["fields"]}
r = client.post("/widget/lead", json={
    "kind": "sales",
    "values": {list(fields)[0]: "Bob", list(fields)[1]: "bob@x.test",
               "injected": "<script>"},
    "product": "mycheckr"})
check(r.status_code == 200, f"public lead submit works ({r.status_code})")
check(set(r.json().keys()) == {"received", "id"}, "lead response does not echo stored data")

leads = client.get("/admin/leads", headers=ADMIN).json()
check(leads["stats"]["total"] == 1, "lead is stored")
stored = json.dumps(leads["leads"][0])
check("injected" not in stored, "unknown form key dropped, not stored")
check("Bob" in stored, "configured field value stored")

r = client.post("/widget/lead", json={"kind": "sales", "values": {}})
check(r.status_code == 400, f"empty form rejected ({r.status_code})")
r = client.post("/widget/lead", json={"kind": "evil", "values": {"a": "b"}})
check(r.status_code == 400, f"unknown form kind rejected ({r.status_code})")

lead_id = leads["leads"][0]["id"]
r = client.post(f"/admin/leads/{lead_id}/handled", headers=ADMIN)
check(r.status_code == 200, "mark lead handled")
check(client.get("/admin/leads", headers=ADMIN).json()["stats"]["unhandled"] == 0,
      "unhandled count drops")

print("\n== faq: manual add, edit, bulk delete ==")
r = client.post("/faq", headers=ADMIN, json={
    "question": "Does it need an internet connection?",
    "answer": "No internet connection is required.", "product": "mycheckr"})
check(r.status_code == 200, f"manual FAQ add ({r.status_code})")
faq_id = r.json()["id"]
check(r.json()["edited"] is True, "manual entry marked edited (protected from redraft)")

r = client.patch(f"/faq/{faq_id}", headers=ADMIN,
                 json={"question": "Is internet needed?", "answer": "No."})
check(r.status_code == 200 and r.json()["question"] == "Is internet needed?",
      "PATCH updates the QUESTION as well as the answer")

# legacy answer-only PATCH must still work
r = client.patch(f"/faq/{faq_id}", headers=ADMIN, json={"answer": "No, it is offline."})
check(r.status_code == 200 and r.json()["question"] == "Is internet needed?",
      "answer-only PATCH leaves question intact (back-compat)")

r = client.delete("/faq", headers=ADMIN)
check(r.status_code == 400, f"bulk delete refused without confirm ({r.status_code})")
r = client.delete("/faq?confirm=true", headers=ADMIN)
check(r.status_code == 200, "bulk delete works with confirm=true")
check(client.get("/faq").json()["faq"] == [], "everything deleted")

print("\n== gaps: recorded from real misses ==")
main.APP_STATE["ready"] = True
for q in ["Does the hopper support Bluetooth pairing?",
          "does the hopper support BLUETOOTH pairing???",
          "What is the warranty period?"]:
    client.post("/query", json={"q": q, "session_id": "s1", "product": "mycheckr"})

g = client.get("/faq/gaps", headers=ADMIN).json()
qs = {x["question"]: x["times_asked"] for x in g["gaps"]}
check(len(g["gaps"]) == 2, f"3 misses deduped to 2 gaps ({len(g['gaps'])})")
check(list(qs.values())[0] == 2, f"near-duplicate counted, not duplicated ({qs})")
check(g["gaps"][0]["times_asked"] >= g["gaps"][-1]["times_asked"], "most-asked first")

# answering a gap resolves it automatically
gap_q = g["gaps"][0]["question"]
client.post("/faq", headers=ADMIN, json={"question": gap_q, "answer": "Yes, over BLE."})
g2 = client.get("/faq/gaps", headers=ADMIN).json()
check(len(g2["gaps"]) == 1, f"answering a gap auto-resolves it ({len(g2['gaps'])})")
check(g2["stats"]["resolved"] == 1, "resolved gap retained for the record, not deleted")

# dismissing noise
remaining = g2["gaps"][0]["id"]
r = client.delete(f"/faq/gaps/{remaining}", headers=ADMIN)
check(r.status_code == 200, "dismiss a junk gap")
check(client.get("/faq/gaps", headers=ADMIN).json()["gaps"] == [], "gap list empty")

print("\n== autogenerate drafting ==")
# Start from a clean store so "nothing was persisted" checks mean exactly that.
client.delete("/faq?confirm=true", headers=ADMIN)
check(client.get("/faq").json()["faq"] == [], "store cleared before drafting checks")

r = client.post("/admin/faq/autogenerate", headers=ADMIN, json={"source": "nope.pdf"})
check(r.status_code == 404, f"unknown source -> 404 ({r.status_code})")

_harness.seed_doc("manual.pdf",
                  ["The MyCheckr operates fully offline. No internet connection is required.",
                   "Power: 12V DC, 2A. Operating range 0-40C."],
                  product="mycheckr", category="biometrics")

_harness.GEN_RESPONSE["text"] = "garbled non-json output from a small model"
r = client.post("/admin/faq/autogenerate", headers=ADMIN,
                json={"source": "manual.pdf", "product": "mycheckr"})
check(r.status_code == 422, f"unparseable model output -> honest 422, nothing stored ({r.status_code})")
check(client.get("/faq").json()["faq"] == [], "no partial garbage persisted")

_harness.GEN_RESPONSE["text"] = '''Sure! Here you go:
```json
[{"question":"Does it need internet?","answer":"No internet connection is required."},
 {"question":"What power does it use?","answer":"12V DC at 2A."}]
```'''
r = client.post("/admin/faq/autogenerate", headers=ADMIN,
                json={"source": "manual.pdf", "product": "mycheckr"})
check(r.status_code == 200 and r.json()["added"] == 2,
      f"drafts parsed out of fenced/prefixed output ({r.json() if r.status_code==200 else r.status_code})")

# re-draft must not duplicate or clobber
r = client.post("/admin/faq/autogenerate", headers=ADMIN,
                json={"source": "manual.pdf", "product": "mycheckr"})
check(r.json()["added"] == 0 and r.json()["skipped_duplicates"] == 2,
      f"re-drafting skips existing questions ({r.json()})")

entries = client.get("/faq").json()["faq"]
drafted = [e for e in entries if e["source"] == "manual.pdf" and not e["edited"]]
check(len(drafted) == 2, f"drafted entries start un-edited, i.e. flagged for review ({len(drafted)})")
target = drafted[0]["id"]
client.patch(f"/faq/{target}", headers=ADMIN,
             json={"question": "Is an internet connection required?",
                   "answer": "No. Reviewed and corrected."})
r = client.post("/admin/faq/autogenerate", headers=ADMIN,
                json={"source": "manual.pdf", "product": "mycheckr"})
check(r.json()["added"] == 0, f"re-draft after an EDIT still dedupes ({r.json()})")
after = [e for e in client.get("/faq").json()["faq"] if e["id"] == target][0]
check(after["answer"] == "No. Reviewed and corrected.", "reviewed answer survived re-drafting")

print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
