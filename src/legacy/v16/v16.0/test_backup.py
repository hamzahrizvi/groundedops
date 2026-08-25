"""Export, inspect and restore — including the encrypted envelope.

The assertions that earn their place are the hostile ones. An archive is
attacker-supplied input the moment someone can upload one, so the zip-slip
family, symlinks and expansion bombs are tested directly rather than
assumed, and the envelope is tested against a wrong passphrase, a flipped
ciphertext byte and a forged plaintext header.

The other load-bearing one is the level split: a support export must not
carry staff password hashes.

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

PW = "test-backup-passphrase"

ok = lambda label: print(f"  PASS  {label}")
fails = []


def check(cond, label):
    if cond:
        ok(label)
    else:
        fails.append(label)
        print(f"  FAIL  {label}")


def names(data, passphrase=PW):
    """Member names, decrypting first if the archive is sealed."""
    if backup.is_encrypted(data):
        data = backup.decrypt_archive(data, passphrase)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return set(zf.namelist())


def export(headers, passphrase=PW, **body):
    payload = dict(body)
    if passphrase is not None:
        payload["passphrase"] = passphrase
    return client.post("/admin/backup/export", headers=headers, json=payload)


# Seed a store so there is content to export. The harness wipes its scratch
# directory on import, so nothing exists until something writes it.
with open(os.environ["FAQ_STORE_PATH"], "w", encoding="utf-8") as _fh:
    json.dump([{"id": "seed", "question": "q", "answer": "a",
                "source": "s", "products": ""}], _fh)

print("\n== export: level boundaries ==")
r = export(ROOT)
check(r.status_code == 200, f"root can export ({r.status_code})")
check("attachment" in r.headers.get("content-disposition", ""),
      "served as a download with a filename")
check(r.headers.get("x-backup-encrypted") == "1", "and it is sealed")
check(".gobk" in r.headers.get("content-disposition", ""),
      "named .gobk, not .zip — it is an envelope, not a zip")
root_zip = r.content

r = export(SUPPORT)
check(r.status_code == 200, f"support can export ({r.status_code})")
support_zip = r.content

check(export(BASIC).status_code == 403,
      "basic cannot export — the archive holds everything its level withholds")
check(client.post("/admin/backup/export", json={"passphrase": PW}).status_code == 401,
      "and an unauthenticated export is refused")

print("\n== export: a passphrase is not optional ==")
r = export(ROOT, passphrase=None)
check(r.status_code == 400,
      f"exporting with no passphrase is refused ({r.status_code})")
check("recover" in (r.json().get("detail") or "").lower(),
      "and says plainly that it cannot be recovered if lost")
r = export(ROOT, passphrase="short")
check(r.status_code == 400, f"a short passphrase is refused ({r.status_code})")

os.environ["BACKUP_ALLOW_PLAINTEXT"] = "1"
try:
    r = export(ROOT, passphrase=None)
    check(r.status_code == 200 and not backup.is_encrypted(r.content),
          "BACKUP_ALLOW_PLAINTEXT permits an unsealed export, deliberately")
    plain_zip = r.content
finally:
    os.environ.pop("BACKUP_ALLOW_PLAINTEXT", None)

print("\n== export: contents scale with level ==")
check("stores/accounts.json" in names(root_zip),
      "a root export carries accounts.json")
check("stores/accounts.json" not in names(support_zip),
      "a support export does NOT — no password hashes below root")
check("stores/policy.json" not in names(support_zip),
      "nor policy.json, which support cannot see in the console either")
check("stores/faq_store.json" in names(support_zip),
      "but it does carry the content support already works with")

m_root = backup.read_manifest(root_zip, PW)
m_supp = backup.read_manifest(support_zip, PW)
check(m_root["includes"]["accounts"] is True and m_supp["includes"]["accounts"] is False,
      "the manifest states which kind it is")
check(m_root["created_by"] == "root@test.local",
      f"and records who made it ({m_root['created_by']})")

print("\n== the envelope ==")
hdr = backup.read_envelope(root_zip)
check(hdr["encrypted"] is True and hdr["format"] == "groundedops-backup",
      "the header identifies the file without any passphrase")
check("counts" in hdr and "created_at" in hdr,
      "and carries enough to pick the right file out of a folder")
check("root@test.local" not in json.dumps(hdr),
      "but no email address — a leaked file gives up as little as possible")
check("accounts.json" not in json.dumps(hdr), "and no file listing")

try:
    backup.read_manifest(root_zip, "the-wrong-passphrase")
    check(False, "a wrong passphrase is refused")
except backup.BackupError as e:
    check("decrypt" in str(e).lower(), f"a wrong passphrase is refused")

try:
    backup.read_manifest(root_zip)
    check(False, "a missing passphrase is refused")
except backup.BackupError as e:
    check("passphrase" in str(e).lower(), "a missing passphrase is refused")

bad = bytearray(root_zip)
bad[-20] ^= 0xFF
try:
    backup.read_manifest(bytes(bad), PW)
    check(False, "a tampered ciphertext is refused")
except backup.BackupError:
    check(True, "a tampered ciphertext is refused — GCM catches it")

# The plaintext header is authenticated as associated data, so editing it to
# lie about the contents must break decryption rather than pass quietly.
off = len(backup.MAGIC)
hlen = int.from_bytes(root_zip[off:off + 4], "big")
h = json.loads(root_zip[off + 4:off + 4 + hlen])
h["sensitive"] = False
nh = json.dumps(h, separators=(",", ":")).encode()
forged = backup.MAGIC + len(nh).to_bytes(4, "big") + nh + root_zip[off + 4 + hlen:]
try:
    backup.read_manifest(forged, PW)
    check(False, "a forged header is refused")
except backup.BackupError:
    check(True, "a forged header is refused — it is bound in as GCM AAD")

check(backup.is_encrypted(plain_zip) is False and
      backup.read_manifest(plain_zip)["format"] == "groundedops-backup",
      "a plain archive still reads, so pre-encryption backups still restore")

print("\n== export: optional parts ==")
r = export(ROOT, documents=False, index=False)
n = names(r.content)
check(not any(x.startswith("documents/") for x in n), "documents can be left out")
check(not any(x.startswith("index/") for x in n), "and so can the index")
check("stores/faq_store.json" in n, "the settings still come along")

print("\n== inspect ==")
r = client.post("/admin/backup/inspect", headers=ROOT,
                files={"file": ("b.gobk", root_zip, "application/octet-stream")})
check(r.status_code == 200 and r.json()["passphrase_required"] is True,
      f"a sealed file identifies itself with no passphrase ({r.status_code})")
check(r.json()["manifest"]["format"] == "groundedops-backup",
      "and the header comes back")

r = client.post("/admin/backup/inspect", headers=ROOT,
                data={"passphrase": PW},
                files={"file": ("b.gobk", root_zip, "application/octet-stream")})
check(r.status_code == 200 and r.json()["manifest"].get("created_by"),
      "with the passphrase, the full manifest comes back")

r = client.post("/admin/backup/inspect", headers=ROOT,
                data={"passphrase": "wrong-passphrase-here"},
                files={"file": ("b.gobk", root_zip, "application/octet-stream")})
check(r.status_code == 400, f"a wrong passphrase is a clean 400 ({r.status_code})")

check(client.post("/admin/backup/inspect", headers=SUPPORT,
                  files={"file": ("b.gobk", root_zip, "application/octet-stream")}
                  ).status_code == 403,
      "support cannot inspect — inspect is part of the restore path")

r = client.post("/admin/backup/inspect", headers=ROOT,
                files={"file": ("x.zip", b"not a zip at all", "application/zip")})
check(r.status_code == 400, f"a non-zip is refused with a 400 ({r.status_code})")

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("manifest.json", json.dumps(
        {"format": "groundedops-backup", "format_version": 99}))
r = client.post("/admin/backup/inspect", headers=ROOT,
                files={"file": ("x.zip", buf.getvalue(), "application/zip")})
check(r.status_code == 400 and "upgrade" in r.json()["detail"].lower(),
      "an archive from a NEWER format version says to upgrade")

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


for bad_name in ("../../etc/passwd",
                 "stores/../../../etc/passwd",
                 "/etc/passwd",
                 "documents/../../../../tmp/evil.txt"):
    r = client.post("/admin/backup/inspect", headers=ROOT,
                    files={"file": ("x.zip", hostile(bad_name), "application/zip")})
    check(r.status_code == 400,
          f"path traversal is refused: {bad_name!r} ({r.status_code})")

sym = hostile("documents/link", b"/etc", external_attr=(0o120777 << 16))
r = client.post("/admin/backup/inspect", headers=ROOT,
                files={"file": ("x.zip", sym, "application/zip")})
check(r.status_code == 400 and "symlink" in r.json()["detail"].lower(),
      f"a symlink member is refused ({r.status_code})")

# Expansion bomb, tested against the ABSOLUTE cap — the bound that exists.
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
backup.MAX_UNCOMPRESSED_BYTES = 1024 * 1024
try:
    r = client.post("/admin/backup/inspect", headers=ROOT,
                    files={"file": ("x.zip", bomb, "application/zip")})
    check(r.status_code == 400,
          f"an archive that expands past the ceiling is refused ({r.status_code})")
    check("expands" in (r.json().get("detail") or "").lower(),
          "and says so, rather than failing obscurely part-way through")
finally:
    backup.MAX_UNCOMPRESSED_BYTES = _was

print("\n== restore ==")
faq_path = os.environ["FAQ_STORE_PATH"]
with open(faq_path, "w", encoding="utf-8") as fh:
    json.dump([{"id": "keep-me", "question": "q", "answer": "a",
                "source": "s", "products": ""}], fh)
snapshot, _ = backup.create_archive(created_by="test", level="root", passphrase=PW)

with open(faq_path, "w", encoding="utf-8") as fh:
    json.dump([], fh)
check(json.load(open(faq_path, encoding="utf-8")) == [], "store emptied")

r = client.post("/admin/backup/import", headers=ROOT, data={"passphrase": PW},
                files={"file": ("b.gobk", snapshot, "application/octet-stream")})
check(r.status_code == 200, f"root can restore a sealed archive ({r.status_code})")
restored = json.load(open(faq_path, encoding="utf-8"))
check(len(restored) == 1 and restored[0]["id"] == "keep-me", "and the store came back")

r_nopw = client.post("/admin/backup/import", headers=ROOT,
                     files={"file": ("b.gobk", snapshot, "application/octet-stream")})
check(r_nopw.status_code == 400,
      f"restoring a sealed archive without its passphrase is refused "
      f"({r_nopw.status_code})")

print("\n== restore: the lock-out footgun ==")
check("accounts.json" in r.json()["skipped"],
      "accounts are SKIPPED by default — restoring an accounts file without "
      "your own account in it locks you out of the console you are using")
r2 = client.post("/admin/backup/import?restore_accounts=true", headers=ROOT,
                 data={"passphrase": PW},
                 files={"file": ("b.gobk", snapshot, "application/octet-stream")})
check(r2.status_code == 200 and "accounts.json" not in r2.json()["skipped"],
      "and are restored only when explicitly asked for")

print("\n== restore: level boundaries ==")
for hdrs, who in ((SUPPORT, "support"), (BASIC, "basic")):
    rr = client.post("/admin/backup/import", headers=hdrs, data={"passphrase": PW},
                     files={"file": ("b.gobk", snapshot, "application/octet-stream")})
    check(rr.status_code == 403, f"{who} cannot restore — it overwrites")

print("\n== restore: the safety snapshot is sealed too ==")
body = r.json()
check(body.get("safety_snapshot"), "a pre-restore snapshot is written")
snap_path = os.path.join(body["safety_snapshot_dir"], body["safety_snapshot"])
check(os.path.isfile(snap_path), "and the file is really on disk")
snap_bytes = open(snap_path, "rb").read()
check(backup.is_encrypted(snap_bytes),
      "and it is ENCRYPTED — it holds what the archive holds, so leaving it "
      "in the clear on disk would undo the encryption")
check(backup.read_manifest(snap_bytes, PW)["format"] == "groundedops-backup",
      "and it is itself a valid, restorable archive")

print("\n== restore: the index needs a restart ==")
r = client.post("/admin/backup/import?restore_index=false", headers=ROOT,
                data={"passphrase": PW},
                files={"file": ("b.gobk", snapshot, "application/octet-stream")})
check(r.json()["restart_required"] is False, "no index restored, no restart claimed")

print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
