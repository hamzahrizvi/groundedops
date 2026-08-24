"""Export, inspect and restore.

The assertions that earn their place are the hostile ones. An archive is
attacker-supplied input the moment someone can upload one, so the zip-slip
family, symlinks and zip bombs are tested directly rather than assumed. The
other load-bearing one is the level split: a support export must not carry
staff password hashes.

_harness redirects every store path into a scratch directory, so nothing
here reads or writes the real install.
"""
import io
import json
import os
import zipfile

import _harness
from fastapi.testclient import TestClient

import backup

app = _harness.app
client = TestClient(app)

ROOT = {"x-admin-password": _harness.ROOT_TOKEN}
SUPPORT = {"x-admin-password": _harness.SUPPORT_TOKEN}
BASIC = {"x-admin-password": _harness.BASIC_TOKEN}

ok = lambda label: print(f"  PASS  {label}")
fails = []


def check(cond, label):
    if cond:
        ok(label)
    else:
        fails.append(label)
        print(f"  FAIL  {label}")


def names(data):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return set(zf.namelist())


# Seed a store so there is content to export. The harness wipes its scratch
# directory on import, so nothing exists until something writes it.
with open(os.environ["FAQ_STORE_PATH"], "w", encoding="utf-8") as _fh:
    json.dump([{"id": "seed", "question": "q", "answer": "a",
                "source": "s", "products": ""}], _fh)

print("\n== export: level boundaries ==")
r = client.get("/admin/backup/export", headers=ROOT)
check(r.status_code == 200, f"root can export ({r.status_code})")
check(r.headers.get("content-type") == "application/zip", "it is a zip")
check("attachment" in r.headers.get("content-disposition", ""),
      "served as a download with a filename")
root_zip = r.content

r = client.get("/admin/backup/export", headers=SUPPORT)
check(r.status_code == 200, f"support can export ({r.status_code})")
support_zip = r.content

check(client.get("/admin/backup/export", headers=BASIC).status_code == 403,
      "basic cannot export — the archive holds everything its level withholds")
check(client.get("/admin/backup/export").status_code == 401,
      "and an unauthenticated export is refused")

print("\n== export: contents scale with level ==")
check("stores/accounts.json" in names(root_zip),
      "a root export carries accounts.json")
check("stores/accounts.json" not in names(support_zip),
      "a support export does NOT — no password hashes below root")
check("stores/policy.json" not in names(support_zip),
      "nor policy.json, which support cannot see in the console either")
check("stores/faq_store.json" in names(support_zip),
      "but it does carry the content support already works with")
check("manifest.json" in names(support_zip), "both carry a manifest")

m_root = json.loads(zipfile.ZipFile(io.BytesIO(root_zip)).read("manifest.json"))
m_supp = json.loads(zipfile.ZipFile(io.BytesIO(support_zip)).read("manifest.json"))
check(m_root["includes"]["accounts"] is True and m_supp["includes"]["accounts"] is False,
      "the manifest states which kind it is")
check(m_root["sensitive"] is True, "a full archive is flagged sensitive")
check(m_root["created_by"] == "root@test.local",
      f"and records who made it ({m_root['created_by']})")
check("password" in m_root["note"].lower(),
      "with a note saying how to store it")

print("\n== export: optional parts ==")
r = client.get("/admin/backup/export?documents=false&index=false", headers=ROOT)
n = names(r.content)
check(not any(x.startswith("documents/") for x in n),
      "documents can be left out")
check(not any(x.startswith("index/") for x in n), "and so can the index")
check("stores/faq_store.json" in n, "the settings still come along")

print("\n== inspect ==")
r = client.post("/admin/backup/inspect", headers=ROOT,
                files={"file": ("b.zip", root_zip, "application/zip")})
check(r.status_code == 200 and r.json()["manifest"]["format"] == "groundedops-backup",
      f"root can inspect an archive without restoring it ({r.status_code})")
check(client.post("/admin/backup/inspect", headers=SUPPORT,
                  files={"file": ("b.zip", root_zip, "application/zip")}
                  ).status_code == 403,
      "support cannot inspect — inspect is part of the restore path")

r = client.post("/admin/backup/inspect", headers=ROOT,
                files={"file": ("x.zip", b"not a zip at all", "application/zip")})
check(r.status_code == 400, f"a non-zip is refused with a 400 ({r.status_code})")

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("stores/faq_store.json", "[]")
r = client.post("/admin/backup/inspect", headers=ROOT,
                files={"file": ("x.zip", buf.getvalue(), "application/zip")})
check(r.status_code == 400 and "manifest" in r.json()["detail"].lower(),
      "a zip with no manifest is refused")

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("manifest.json", json.dumps(
        {"format": "groundedops-backup", "format_version": 99}))
r = client.post("/admin/backup/inspect", headers=ROOT,
                files={"file": ("x.zip", buf.getvalue(), "application/zip")})
check(r.status_code == 400 and "upgrade" in r.json()["detail"].lower(),
      "an archive from a NEWER format version says to upgrade rather than "
      "half-reading it")

print("\n== hostile archives ==")
def hostile(member_name, content=b"x", external_attr=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("manifest.json", json.dumps(
            {"format": "groundedops-backup", "format_version": 1}))
        info = zipfile.ZipInfo(member_name)
        if external_attr is not None:
            info.external_attr = external_attr
        zf.writestr(info, content)
    return b.getvalue()

for bad in ("../../etc/passwd",
            "stores/../../../etc/passwd",
            "/etc/passwd",
            "documents/../../../../tmp/evil.txt"):
    r = client.post("/admin/backup/inspect", headers=ROOT,
                    files={"file": ("x.zip", hostile(bad), "application/zip")})
    check(r.status_code == 400,
          f"path traversal is refused: {bad!r} ({r.status_code})")

# A symlink member lets a later member write through it to anywhere.
sym = hostile("documents/link", b"/etc", external_attr=(0o120777 << 16))
r = client.post("/admin/backup/inspect", headers=ROOT,
                files={"file": ("x.zip", sym, "application/zip")})
check(r.status_code == 400 and "symlink" in r.json()["detail"].lower(),
      f"a symlink member is refused ({r.status_code})")

# Zip bomb: a small file that expands to far more than the install allows.
# Tested against the ABSOLUTE cap, which is the bound that actually exists.
# A per-member compression-ratio check was tried and removed: Chroma's
# zero-padded index files compress past any ratio worth setting, so it made
# the module refuse its own exports. See backup.py.
def bomb_zip(payload_bytes):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr("manifest.json", json.dumps(
            {"format": "groundedops-backup", "format_version": 1}))
        zf.writestr("documents/big.bin", bytes(payload_bytes))
    return b.getvalue()

bomb = bomb_zip(5 * 1024 * 1024)
check(len(bomb) < 60 * 1024,
      f"the bomb really is small on disk ({len(bomb)} bytes) but 5MB expanded")

_was = backup.MAX_UNCOMPRESSED_BYTES
backup.MAX_UNCOMPRESSED_BYTES = 1024 * 1024        # 1MB ceiling for this check
try:
    r = client.post("/admin/backup/inspect", headers=ROOT,
                    files={"file": ("x.zip", bomb, "application/zip")})
    check(r.status_code == 400,
          f"an archive that expands past the ceiling is refused ({r.status_code})")
    check("expands" in (r.json().get("detail") or "").lower(),
          "and says so, rather than failing obscurely part-way through")
finally:
    backup.MAX_UNCOMPRESSED_BYTES = _was

r = client.post("/admin/backup/inspect", headers=ROOT,
                files={"file": ("x.zip", bomb, "application/zip")})
check(r.status_code == 200,
      f"the same archive is fine under the normal ceiling ({r.status_code})")

print("\n== restore ==")
faq_path = os.getenv("FAQ_STORE_PATH")
with open(faq_path, "w", encoding="utf-8") as fh:
    json.dump([{"id": "keep-me", "question": "q", "answer": "a",
                "source": "s", "products": ""}], fh)
snapshot, _ = backup.create_archive(created_by="test", level="root")

# Change the store, then restore over it.
with open(faq_path, "w", encoding="utf-8") as fh:
    json.dump([], fh)
check(json.load(open(faq_path, encoding="utf-8")) == [], "store emptied")

r = client.post("/admin/backup/import", headers=ROOT,
                files={"file": ("b.zip", snapshot, "application/zip")})
check(r.status_code == 200, f"root can restore ({r.status_code})")
restored = json.load(open(faq_path, encoding="utf-8"))
check(len(restored) == 1 and restored[0]["id"] == "keep-me",
      "and the store came back")
check("faq_store.json" in r.json()["restored"]["stores"],
      "the response names what it restored")

print("\n== restore: the lock-out footgun ==")
check("accounts.json" in r.json()["skipped"],
      "accounts are SKIPPED by default — restoring an accounts file without "
      "your own account in it locks you out of the console you are using")
r2 = client.post("/admin/backup/import?restore_accounts=true", headers=ROOT,
                 files={"file": ("b.zip", snapshot, "application/zip")})
check(r2.status_code == 200 and "accounts.json" not in r2.json()["skipped"],
      "and are restored only when explicitly asked for")

print("\n== restore: level boundaries ==")
check(client.post("/admin/backup/import", headers=SUPPORT,
                  files={"file": ("b.zip", snapshot, "application/zip")}
                  ).status_code == 403,
      "support cannot restore — it overwrites")
check(client.post("/admin/backup/import", headers=BASIC,
                  files={"file": ("b.zip", snapshot, "application/zip")}
                  ).status_code == 403,
      "basic cannot restore")

print("\n== restore: a safety snapshot is taken first ==")
body = r.json()
check(body.get("safety_snapshot"),
      f"the pre-restore state is saved ({body.get('safety_snapshot')})")
snap_path = os.path.join(body["safety_snapshot_dir"], body["safety_snapshot"])
check(os.path.isfile(snap_path), "and the file is really on disk")
check(backup.read_manifest(open(snap_path, "rb").read())["format"] == "groundedops-backup",
      "and it is itself a valid, restorable archive")

print("\n== restore: the index needs a restart ==")
r = client.post("/admin/backup/import?restore_index=false", headers=ROOT,
                files={"file": ("b.zip", snapshot, "application/zip")})
check(r.json()["restart_required"] is False,
      "no index restored, no restart claimed")

print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
