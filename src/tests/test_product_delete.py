"""Deleting a product, and what happens to everything filed under it.

The bug this covers is not hypothetical: deleting a product used to remove
only its catalogue row, leaving documents, answers and questions tagged to
a key with no product in front of it. The live install still carries two
such keys (`nv9st`, `coin_hoppers`) with real content behind them,
invisible to a console that builds every list from the catalogue.

So the assertions that matter are: nothing is ever left orphaned, and a
caller who does not say what should happen is refused rather than defaulted
in either direction.

Also covers the gap-log noise filter, which is why "talk to sales" and "no"
were sitting on the customer-questions backlog.
"""
import _harness
from fastapi.testclient import TestClient

import catalog as catalog_mod
import faq_store

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


def fresh_product(cat, key, name):
    try:
        catalog_mod.add_category(cat, cat.title())
    except Exception:
        pass
    try:
        catalog_mod.add_product(cat, key, name)
    except Exception:
        pass


print("\n== the gap log only takes real questions ==")
for noise in ["talk to sales", "speak to support", "no", "yes", "thanks",
              "nv9usb", "sales", "hi"]:
    check(not faq_store.is_curatable_question(noise),
          f"rejected as noise: {noise!r}")
for real in ["what is the pinout for nv9?",
             "How do I set up RNDIS between an ICU device and a local network",
             "does the mycheckr have an ethernet port"]:
    check(faq_store.is_curatable_question(real),
          f"kept as a real question: {real[:44]!r}")

check(faq_store.is_curatable_question("who do I talk to about sales pricing"),
      "an intent phrase INSIDE a real question does not make it noise")

before = len(faq_store.list_gaps(None, include_resolved=True, include_spam=True))
faq_store.record_gap("talk to sales", "p_noise")
faq_store.record_gap("no", "p_noise")
after = len(faq_store.list_gaps(None, include_resolved=True, include_spam=True))
check(after == before, "record_gap drops noise rather than filing it")

print("\n== deleting a product must say what happens to its content ==")
fresh_product("cat_a", "p_doomed", "Doomed")
fresh_product("cat_a", "p_target", "Target")
faq_store.add_entry("what is doomed", "an answer", products="p_doomed")
faq_store.record_gap("what colour is the doomed unit", "p_doomed")

r = client.delete("/admin/product/cat_a/p_doomed", headers=ADMIN)
check(r.status_code == 400,
      f"refused with no instruction — not defaulted either way ({r.status_code})")
check("reassign_to" in (r.json().get("detail") or ""),
      "and the error says what to pass instead")
check(any(p["key"] == "p_doomed"
          for c in catalog_mod.catalog()["categories"]
          for p in c.get("products", [])),
      "the product is still there after a refused delete")

r = client.delete("/admin/product/cat_a/p_doomed"
                  "?reassign_to=p_target&delete_content=true", headers=ADMIN)
check(r.status_code == 400, "asking for both reassign AND delete is refused")

r = client.delete("/admin/product/cat_a/p_doomed?reassign_to=nope", headers=ADMIN)
check(r.status_code == 400,
      "reassigning to a product that does not exist is refused — that would "
      "just orphan the content again")

print("\n== reassigning moves everything ==")
r = client.delete("/admin/product/cat_a/p_doomed?reassign_to=p_target",
                  headers=ADMIN)
check(r.status_code == 200, f"reassign succeeds ({r.status_code})")
check(r.json()["reassigned_to"] == "p_target", "and reports where it went")

check(not any(p["key"] == "p_doomed"
              for c in catalog_mod.catalog()["categories"]
              for p in c.get("products", [])),
      "the product is gone from the catalogue")
moved = [e for e in faq_store.list_for_product("p_target")
         if e["question"] == "what is doomed"]
check(len(moved) == 1, "its answer now belongs to the target product")
gaps = [g for g in faq_store.list_gaps("p_target")
        if "doomed unit" in g["question"]]
check(len(gaps) == 1, "and so does its unanswered question")
check(not faq_store.list_gaps("p_doomed"),
      "nothing is left behind under the deleted key")

print("\n== deleting takes the content with it ==")
fresh_product("cat_a", "p_gone", "Gone")
faq_store.add_entry("what is gone", "an answer", products="p_gone")
faq_store.record_gap("what colour is the gone unit", "p_gone")

r = client.delete("/admin/product/cat_a/p_gone?delete_content=true", headers=ADMIN)
check(r.status_code == 200, f"delete succeeds ({r.status_code})")
check(r.json()["affected"]["answers"] >= 1, "and reports what it removed")
check(not faq_store.list_for_product("p_gone"), "its answers are gone")
check(not faq_store.list_gaps("p_gone"), "its questions are gone")

print("\n== content shared with another product is never destroyed ==")
fresh_product("cat_a", "p_share1", "Share One")
fresh_product("cat_a", "p_share2", "Share Two")
faq_store.add_entry("shared answer", "text", products="p_share1,p_share2")

r = client.delete("/admin/product/cat_a/p_share1?delete_content=true",
                  headers=ADMIN)
check(r.status_code == 200, "deleting one of two owners succeeds")
kept = [e for e in faq_store.list_for_product("p_share2")
        if e["question"] == "shared answer"]
check(len(kept) == 1,
      "the shared answer survives — it still belongs to a product nobody "
      "touched")

print("\n== retagging an already-orphaned key ==")
# This is the repair path for content whose product was deleted BEFORE
# deletion cascaded, which is the state the live install is in.
faq_store.add_entry("orphan question", "text", products="ghost_key")
faq_store.record_gap("what does the ghost do", "ghost_key")
fresh_product("cat_a", "p_home", "Home")

r = client.post("/admin/product/retag", headers=ADMIN,
                json={"from_key": "ghost_key", "to_key": "p_home"})
check(r.status_code == 200, f"retag succeeds ({r.status_code})")
check(r.json()["answers"] >= 1 and r.json()["questions"] >= 1,
      "and reports both halves")
check(any(e["question"] == "orphan question"
          for e in faq_store.list_for_product("p_home")),
      "the orphaned answer now has a real product")
check(not faq_store.list_gaps("ghost_key"),
      "and nothing is still filed under the ghost key")

r = client.post("/admin/product/retag", headers=ADMIN,
                json={"from_key": "ghost_key", "to_key": "still_not_real"})
check(r.status_code == 400,
      "retagging ONTO a nonexistent product is refused — that recreates the "
      "problem")

print("\n== the console can preview what a delete would affect ==")
fresh_product("cat_a", "p_preview", "Preview")
faq_store.add_entry("preview q", "a", products="p_preview")
r = client.get("/admin/product/p_preview/contents", headers=ADMIN)
check(r.status_code == 200 and r.json()["contents"]["answers"] >= 1,
      "contents reports what is filed under a product before deleting it")

print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
