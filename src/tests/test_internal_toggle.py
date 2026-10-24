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

import routes_documents

marked = []
retrieval_db.set_internal = lambda sources, on, reindex=True: marked.append((list(sources), on))
# Marking takes the chunks out of the index (D-20261024-06), and the FAQ
# search reads their text: it has to run before the mark.
_faqs = routes_documents.related_faqs
faqs_seen_marks = []
routes_documents.related_faqs = lambda s: (faqs_seen_marks.append(len(marked)), _faqs(s))[1]
faq_store.merge_questions(DOC, "biometrics_general",
                          [{"question": "How is pulse configured?", "answer": "Set it."}])

r = client.post("/admin/source_internal", json={"sources": [DOC], "internal": True}, headers=ROOT)
assert r.status_code == 200, r.text
assert marked == [([DOC], True)], marked
assert faqs_seen_marks == [0], "related FAQs were looked up after marking"
assert [f["question"] for f in r.json()["faqs"]
        if f["derived"]] == ["How is pulse configured?"], r.json()

r = client.post("/admin/source_internal", json={"sources": [DOC], "internal": False}, headers=ROOT)
assert r.status_code == 200 and marked[-1] == ([DOC], False) and r.json()["faqs"] == []
assert client.post("/admin/source_internal", json={"sources": [DOC], "internal": True}).status_code in (401, 403)
# Removing a synced Confluence document on the Documents page excludes it
# there too, or the Confluence page keeps saying "For customers" and the next
# edit in Confluence adds it back.
import confluence_sync
confluence_sync._update(lambda st: st["items"].update({"att:1": {
    "kind": "file", "id": "1", "title": "Sync.pdf", "source": "Sync.pdf", "present": True,
    "decision": "approved", "version": 3, "synced_version": 3}}))
r = client.post("/delete_source", json={"source": "Sync.pdf"}, headers=ROOT)
assert r.status_code == 200, r.text
it = confluence_sync._load()["items"]["att:1"]
assert it["decision"] == "excluded" and it["synced_version"] is None, it
assert confluence_sync.status_of(it) == "excluded"
print("PASS internal toggle")
