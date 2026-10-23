"""Upload sets the internal mark before indexing; a replacement inherits it;
an internal document is not downloadable."""
import types

import _harness
from fastapi.testclient import TestClient

import retrieval_db
import routes_documents

marks = set()
retrieval_db.internal_sources = lambda: frozenset(marks)
retrieval_db.set_internal = lambda sources, on: (
    marks.update(sources) if on else marks.difference_update(sources))
retrieval_db.is_excluded = lambda s: (s or "") in marks

client = TestClient(_harness.app)
ADMIN = {"x-admin-password": _harness.SUPPORT_TOKEN}
fails = []


def check(cond, label):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        fails.append(label)


routes_documents.capability = lambda name: True
routes_documents.threading = types.SimpleNamespace(
    Thread=lambda **k: types.SimpleNamespace(start=lambda: None))

scope = {"category-key": "c", "product-key": "p"}


def up(name, **hdr):
    return client.post("/upload", files={"file": (name, b"x")},
                       headers={**ADMIN, **scope, **hdr})


r = up("Upload Internal.txt", internal="1")
check(r.status_code == 200 and r.json().get("internal") is True
      and "Upload Internal.txt" in marks, "upload with internal=1 is marked")
r = up("Upload Public.txt")
check(r.status_code == 200 and r.json().get("internal") is False
      and "Upload Public.txt" not in marks, "upload without the header is public")
r = up("Upload Odd.txt", internal="on")
check(r.status_code == 400 and "Upload Odd.txt" not in marks, "unknown header value refused")
marks.add("Old Note.txt")
r = up("New Note.txt", **{"replace-source": "Old Note.txt"})
check(r.status_code == 200 and "New Note.txt" in marks, "a replacement inherits the mark")
marks.add("Stale.txt")
r = up("Stale.txt")
check(r.status_code == 200 and "Stale.txt" not in marks, "a lingering mark is cleared")
check(client.get("/source_file/Upload Internal.txt").status_code == 404,
      "internal document: source_file 404")

if fails:
    print(f"{len(fails)} FAILURE(S):", *fails, sep="\n  - ")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
