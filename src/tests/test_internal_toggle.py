"""The Documents page marks a document internal, and offers the FAQs made
from it for removal (retrieval_db.is_excluded does the hiding; see
test_internal_documents)."""
import _harness
from fastapi.testclient import TestClient

import faq_store
import retrieval_db

client = TestClient(_harness.app)
ROOT = {"x-admin-password": _harness.ROOT_TOKEN}
DOC = "Biometric Pulse Configuration Walkthrough.txt"

marked = []
retrieval_db.set_internal = lambda sources, on: marked.append((list(sources), on))
faq_store.merge_questions(DOC, "biometrics_general",
                          [{"question": "How is pulse configured?", "answer": "Set it."}])

r = client.post("/admin/source_internal", json={"sources": [DOC], "internal": True}, headers=ROOT)
assert r.status_code == 200, r.text
assert marked == [([DOC], True)], marked
assert [f["question"] for f in r.json()["faqs"]
        if f["derived"]] == ["How is pulse configured?"], r.json()

r = client.post("/admin/source_internal", json={"sources": [DOC], "internal": False}, headers=ROOT)
assert r.status_code == 200 and marked[-1] == ([DOC], False) and r.json()["faqs"] == []
assert client.post("/admin/source_internal", json={"sources": [DOC], "internal": True}).status_code in (401, 403)
print("PASS internal toggle")
