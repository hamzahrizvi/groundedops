"""End-to-end checks for the FAQ/gaps/autogenerate routes, run against
FastAPI's TestClient with retrieval/generation stubbed. Focus: auth
boundaries, validation, persistence, and the public/admin split.

Widget config and lead capture are NOT covered here: the real embeddable
widget (groundedops-widget.js) reads its branding from data-* attributes
and has no sales/support form, so widget_config.py is intentionally left
unwired in main.py pending that frontend work (see PROJECT_MAP.md). Those
routes don't exist in this app and testing them here would just assert
against a feature that hasn't shipped.
"""
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
for method, path in [("get", "/faq/gaps"), ("post", "/admin/faq/autogenerate")]:
    kw = {"json": {"source": "x"}} if method == "post" else {}
    r = getattr(client, method)(path, headers=BAD, **kw)
    check(r.status_code == 401, f"{method.upper()} {path} rejects bad password ({r.status_code})")

# Public routes must NOT require a password.
r = client.get("/widget/config")
check(r.status_code == 200, f"GET /widget/config is public ({r.status_code})")

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

print("\n== gap bulk actions: dismiss, spam, answer-with-AI ==")
client.delete("/faq?confirm=true", headers=ADMIN)  # clean slate

for q in ["Does the gadget support satellite uplink?",
          "Does the gadget support satellite uplink??",
          "Is there a spam question here"]:
    client.post("/query", json={"q": q, "session_id": "s2"})

g = client.get("/faq/gaps", headers=ADMIN).json()["gaps"]
check(len(g) == 2, f"2 distinct gaps recorded before bulk actions ({len(g)})")
dismiss_id = next(x["id"] for x in g if "satellite" in x["question"])
spam_id = next(x["id"] for x in g if "spam" in x["question"])
spam_q = next(x["question"] for x in g if x["id"] == spam_id)
dismiss_q = next(x["question"] for x in g if x["id"] == dismiss_id)

r = client.request("DELETE", "/faq/gaps", headers=ADMIN, json={"ids": [dismiss_id]})
check(r.status_code == 200 and r.json()["deleted"] == 1,
      f"bulk dismiss removed 1 ({r.json() if r.status_code == 200 else r.status_code})")

r = client.post("/faq/gaps/spam", headers=ADMIN, json={"ids": [spam_id]})
check(r.status_code == 200 and r.json()["marked"] == 1,
      f"bulk mark-spam flagged 1 ({r.json() if r.status_code == 200 else r.status_code})")

check(client.get("/faq/gaps", headers=ADMIN).json()["gaps"] == [], "both gone from the default list")

client.post("/query", json={"q": spam_q, "session_id": "s3"})
check(client.get("/faq/gaps", headers=ADMIN).json()["gaps"] == [],
      "spam-flagged question does not resurface on a repeat ask")

client.post("/query", json={"q": dismiss_q, "session_id": "s4"})
check(len(client.get("/faq/gaps", headers=ADMIN).json()["gaps"]) == 1,
      "dismissed question starts a fresh gap on a repeat ask (unlike spam)")

remaining = client.get("/faq/gaps", headers=ADMIN).json()["gaps"][0]
r = client.post("/admin/faq/answer_gap", headers=ADMIN, json={"gap_id": remaining["id"]})
check(r.status_code == 200 and "answer" in r.json(),
      f"answer_gap returns a draft ({r.status_code})")
check(client.get("/faq").json()["faq"] == [], "draft is NOT auto-saved to the FAQ")

r = client.post("/admin/faq/answer_gap", headers=ADMIN, json={"gap_id": "does-not-exist"})
check(r.status_code == 404, f"answer_gap on an unknown id -> 404 ({r.status_code})")

print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
