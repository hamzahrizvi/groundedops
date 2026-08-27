"""Grouping near-duplicate customer questions, and the filter/sort controls.

The assertions that matter are the ones about NOT merging. A clusterer that
folds everything together produces a tidy short list that is wrong, and the
failure is invisible — the questions are still counted, just under the wrong
heading. So the false-merge cases are tested explicitly, not just the
true-merge ones.

Uses the lexical fallback path (embeddings are stubbed out by _harness), so
these run without the model. The semantic path was calibrated separately
against the real 82-question gap log; see GAP_CLUSTER_THRESHOLD's comment.
"""
import _harness
from fastapi.testclient import TestClient

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


def gap(q, scope="p1", asked=1, ts=0):
    return {"id": faq_store._gap_key(q), "question": q, "scope": scope,
            "times_asked": asked, "ts": ts, "resolved": False, "spam": False,
            "reason": ""}


print("\n== clustering folds rewordings together ==")
# Lexical similarity on content words: these share "pinout"/"nv9" and differ
# only in filler, so they group without needing the embedding model.
rows = [gap("what is the pinout for nv9", asked=5),
        gap("what is the pinout for the nv9", asked=3),
        gap("give nv9 pinout", asked=2)]
cl = faq_store.cluster_gaps(rows, threshold=0.4)
check(len(cl) == 1, f"three phrasings of one question become one entry ({len(cl)})")
check(cl[0]["times_asked"] == 10,
      f"and the ask count is the SUM, i.e. real demand ({cl[0]['times_asked']})")
check(cl[0]["cluster_size"] == 3, "cluster_size reports how many it stands for")
check(len(cl[0]["variants"]) == 2, "the other wordings are carried, not discarded")
check(sorted(cl[0]["member_ids"]) == sorted(r["id"] for r in rows),
      "every underlying gap id is listed, so a bulk action can reach them all")

print("\n== clustering does NOT merge different questions ==")
rows = [gap("does the mycheckr have an ethernet port"),
        gap("how can the mycheckr be mounted"),
        gap("what hardware does the mycheckr include")]
cl = faq_store.cluster_gaps(rows, threshold=0.4)
check(len(cl) == 3,
      f"three different questions about one product stay separate ({len(cl)})")

print("\n== products are never merged across ==")
rows = [gap("what is the pinout", scope="nv9usb", asked=4),
        gap("what is the pinout", scope="sku_scs", asked=2)]
cl = faq_store.cluster_gaps(rows, threshold=0.1)   # would merge on text alone
check(len(cl) == 2,
      "the same question under two products stays two backlog items, even at "
      f"a threshold low enough to merge anything ({len(cl)})")

print("\n== the cluster carries the most recent ask ==")
rows = [gap("what is the pinout for nv9", asked=9, ts=100),
        gap("what is the pinout for the nv9", asked=1, ts=500)]
cl = faq_store.cluster_gaps(rows, threshold=0.4)
check(cl[0]["ts"] == 500,
      f"so 'most recent' sorts on the newest question in the group ({cl[0]['ts']})")
check(cl[0]["question"] == "what is the pinout for nv9",
      "but the headline stays the most-asked phrasing")

print("\n== clustering never rewrites the stored gaps ==")
rows = [gap("what is the pinout for nv9", asked=5),
        gap("give nv9 pinout", asked=2)]
before = [dict(r) for r in rows]
faq_store.cluster_gaps(rows, threshold=0.4)
check(rows == before,
      "the input list is untouched — a wrong merge is a display problem, "
      "not a data loss")

print("\n== the endpoint: grouping, sorting, filtering ==")
faq_store.record_gap("what is the pinout for nv9", "nv9usb")
faq_store.record_gap("give nv9 pinout", "nv9usb")
faq_store.record_gap("how do I mount it", "mycheckr")
faq_store.record_gap("something with no product", None)

r = client.get("/faq/gaps", headers=ADMIN)
check(r.status_code == 200, f"the gaps endpoint answers ({r.status_code})")
body = r.json()
check("scopes" in body,
      "and offers the scope list the filter control is built from")
check("nv9usb" in body["scopes"] and "mycheckr" in body["scopes"],
      "which includes every scope that has questions filed under it")
check(body["grouped"] is True, "grouping is on by default")

r = client.get("/faq/gaps?group_similar=false", headers=ADMIN)
check(all("variants" not in g for g in r.json()["gaps"]),
      "turning grouping off returns the raw list, ungrouped")

r = client.get("/faq/gaps?product=mycheckr", headers=ADMIN)
check(all(g["scope"] == "mycheckr" for g in r.json()["gaps"]),
      "filtering by product returns only that product's questions")

r = client.get("/faq/gaps?product=__none__", headers=ADMIN)
got = r.json()["gaps"]
check(got and all(not g.get("scope") for g in got),
      "and __none__ returns the ones recorded with no product at all")

r = client.get("/faq/gaps?sort=latest", headers=ADMIN)
ts = [g.get("ts") or 0 for g in r.json()["gaps"]]
check(ts == sorted(ts, reverse=True),
      "sort=latest really is newest-first")

r = client.get("/faq/gaps?sort=product", headers=ADMIN)
scopes = [(g.get("scope") or "￿") for g in r.json()["gaps"]]
check(scopes == sorted(scopes), "sort=product groups the scopes together")

r = client.get("/faq/gaps?sort=demand", headers=ADMIN)
asks = [g.get("times_asked", 1) for g in r.json()["gaps"]]
check(asks == sorted(asks, reverse=True), "sort=demand is most-asked first")

print("\n== still admin-only ==")
check(client.get("/faq/gaps").status_code == 401,
      "an unauthenticated read is refused")
check(client.get("/faq/gaps",
                 headers={"x-admin-password": _harness.BASIC_TOKEN}
                 ).status_code == 403,
      "and basic cannot read customer questions")

print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
