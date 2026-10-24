"""Upload sets the internal mark before indexing; a replacement inherits it;
an internal document is not downloadable."""
import types

import _harness
from fastapi.testclient import TestClient

import retrieval_db
import routes_documents

marks = set()
calls = []     # (sources, on, reindex): who asks set_internal to re-index
retrieval_db.internal_sources = lambda: frozenset(marks)
retrieval_db.is_marked_internal = lambda s: (s or "") in marks
retrieval_db.set_internal = lambda sources, on, reindex=True: (
    calls.append((list(sources), on, reindex)),
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
# D-20261024-06: the upload's worker indexes the file; an un-mark that
# re-indexed the old original first would make it skip the upload.
check(calls[-1] == (["Stale.txt"], False, False), f"upload un-marks without re-indexing: {calls[-1]}")


def _unwritable(sources, on, reindex=True):
    raise RuntimeError("internal_sources.json unreadable")


_ok_set = retrieval_db.set_internal
retrieval_db.set_internal = _unwritable
marks.add("Held.txt")          # what a fail-closed set says of every name
r = up("Held.txt", internal="1")
check(r.status_code == 500, "internal upload refused when its mark cannot be written")
retrieval_db.set_internal = _ok_set
check(client.get("/source_file/Upload Internal.txt").status_code == 404,
      "internal document: source_file 404")
# Remove on the Documents page keeps the mark (the original stays
# undownloadable); the sync's Remove drops it WITHOUT re-indexing, or the
# sync's own un-mark afterwards would bring the removed document back.
marks.add("Gone.txt")
r = client.post("/delete_source", json={"source": "Gone.txt"},
                headers={"x-admin-password": _harness.ROOT_TOKEN})
check(r.status_code == 200 and "Gone.txt" in marks, "Remove keeps the internal mark")
calls.clear()
routes_documents.remove_source_now("Gone.txt")
check(calls == [(["Gone.txt"], False, False)], f"sync Remove un-marks without re-indexing: {calls}")

if fails:
    print(f"{len(fails)} FAILURE(S):", *fails, sep="\n  - ")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
