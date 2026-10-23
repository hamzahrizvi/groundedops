"""A document marked internal on the Documents page must not reach the public.

Marking stays indexed (staff still see it) but every public read refuses it:
answers, downloads, figures, "view source". The FAQ list offered for removal
must include the entries made from it, ticked by the console.
"""
import os

import _harness
from fastapi.testclient import TestClient

import docstore
import faq_store
import main

client = TestClient(_harness.app)
ROOT = {"x-admin-password": _harness.ROOT_TOKEN}
DOC = "Secret Pulse Walkthrough.txt"

os.makedirs(docstore.store_dir(), exist_ok=True)
with open(os.path.join(docstore.store_dir(), DOC), "w", encoding="utf-8") as f:
    f.write("internal content")
faq_store.merge_questions(DOC, "biometrics_general",
                          [{"question": "How is pulse configured?", "answer": "Set it."}])

assert not main.is_internal_source(DOC)

r = client.post("/admin/source_internal", json={"source": DOC, "internal": True}, headers=ROOT)
assert r.status_code == 200, r.text
assert main.is_internal_source(DOC) and main.is_internal_source(DOC.upper())
assert not main.is_internal_source("Walkthrough.txt"), "exact name, not a substring"
assert any(f["why"] == "made from this document" for f in r.json()["faqs"]), r.json()
assert client.get("/source_file/" + DOC).status_code == 404
assert main._public_only([{"source": DOC}, {"source": "public.pdf"}]) == [{"source": "public.pdf"}]

r = client.post("/admin/source_internal", json={"source": DOC, "internal": False}, headers=ROOT)
assert r.status_code == 200 and not main.is_internal_source(DOC)
print("PASS internal documents")
