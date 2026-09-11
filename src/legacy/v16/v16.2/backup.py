"""Export and restore everything this install would be sorry to lose.

WHY
---
Several pieces of state here are not regenerable and not in git:
`accounts.json` (scrypt password hashes — losing it loses every account),
`documents/` (the only copy of some source PDFs), the curated FAQ, the
catalogue, the widget config and the customer enquiries. `PENDING.md` has
carried "decide how to back this up" as a blocking item for a while; this
is that decision made.

The vector index is included too, even though it is derived. Rebuilding it
from the documents works (`reindex.py --from-store`) but costs a full parse
and embed of every file, which is exactly the "have to re-ingest" that this
is meant to avoid. It is also small — under 10MB against ~100MB of PDFs —
so including it is close to free.

WHAT AN ARCHIVE IS
------------------
A zip with a `manifest.json` at the root:

    manifest.json
    stores/*.json          faq, catalogue, widget config, leads, accounts…
    db/conversations.db
    index/…                the Chroma directory
    documents/…            the retained originals

ENCRYPTION
----------
That zip is then sealed in an encrypted envelope (`.gobk`), because an
archive holds staff password hashes and customer contact details and will
end up on a NAS, in an email, or on someone's laptop.

    b"GOBK1\n" | 4-byte header length | plaintext JSON header | AES-256-GCM

The key is scrypt-derived from a passphrase. The header stays readable so a
file can be identified — what it is, when it was made, roughly what is in
it — WITHOUT the passphrase, and it is authenticated as GCM associated data
so it cannot be edited. It deliberately carries no email addresses; who made
a backup is inside the encrypted part.

What this cannot do, and it is worth being plain about: there is no way to
make a file that ONLY this application can open. Any key baked into the code
would sit in a public repository. Encryption protects the file at rest from
whoever finds it; the passphrase is the thing that does the protecting, so
it has to be held somewhere other than in the backup.

LOSING THE PASSPHRASE LOSES THE BACKUP. There is no recovery path, by
design — a recovery path is another way in. Reading a plain archive is
still supported, so backups taken before this existed still restore.

SENSITIVITY
-----------
A full archive contains staff password hashes and customer contact
details. Treat the file like a password database. Export is gated at
`support`, and the CONTENTS SCALE WITH LEVEL: only a root export carries
`accounts.json` and `policy.json`. Import is root-only, because it
overwrites.

RESTORING IS DANGEROUS, SO
--------------------------
- A safety snapshot of the current state is taken automatically before
  anything is overwritten. A bad restore is then recoverable.
- Accounts are NOT restored unless explicitly asked for: restoring an
  accounts file that does not contain your own account locks you out of the
  console you are standing in.
- The index is written to disk but needs a restart to take effect. Chroma
  holds an open sqlite connection to those files; swapping them under a
  live client risks corrupting the thing you were trying to restore.
"""
import io
import json
import logging
import os
import shutil
import zipfile
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

FORMAT = "groundedops-backup"
FORMAT_VERSION = 1

# Refuse an archive that would expand to more than this. A zip is
# attacker-supplied input the moment someone can upload one, and a small
# file that expands to fill the disk is the oldest trick there is.
MAX_UNCOMPRESSED_BYTES = int(os.getenv("BACKUP_MAX_BYTES", str(4 * 1024 * 1024 * 1024)))

# There is deliberately NO per-member compression-ratio check. The obvious
# "refuse anything that expands more than Nx" heuristic was tried and had to
# come out: Chroma's HNSW index files (data_level0.bin) are zero-padded and
# compress well past 1000:1, so a 200:1 ceiling made this module refuse its
# own exports. A ratio high enough to pass them is high enough to be
# meaningless. The absolute total above is the bound that actually matters,
# and it is checked as members are walked rather than after extraction.

# Directories inside the archive that may be written on restore. Anything
# else is ignored rather than trusted — see _safe_members.
_ALLOWED_PREFIXES = ("stores/", "db/", "index/", "documents/")


class BackupError(Exception):
    """Anything the operator should see as a 400."""


# ── encrypted envelope ────────────────────────────────────────────────

MAGIC = b"GOBK1\n"
ENVELOPE_EXT = ".gobk"
MIN_PASSPHRASE = 12

# scrypt cost for deriving the file key. Higher than the per-login cost in
# accounts.py: this runs twice in the life of a backup, so ~0.5s is free,
# and it is the only thing standing between a leaked archive and its
# contents. maxmem is passed explicitly because 2**16 needs ~64MB and
# OpenSSL's default ceiling is lower than that.
_KDF_N = 2 ** 16
_KDF_R = 8
_KDF_P = 1
_KDF_MAXMEM = 128 * _KDF_N * _KDF_R * 2


def _aesgcm():
    """Imported here, not at module scope, on purpose. main.py imports this
    module at startup, and the CI `unit` job installs an explicit package
    list rather than requirements.txt — a top-level import would take the
    whole app down there instead of failing only the feature that needs it.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except ImportError:
        raise BackupError(
            "Encrypted backups need the 'cryptography' package, which is not "
            "installed here. Install it, or export without a passphrase.")


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    import hashlib
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=_KDF_N,
                          r=_KDF_R, p=_KDF_P, dklen=32, maxmem=_KDF_MAXMEM)


def check_passphrase(passphrase: str) -> None:
    """Length only, same reasoning as accounts.py: composition rules push
    people towards 'Password1!' and length is what actually helps."""
    if len(passphrase or "") < MIN_PASSPHRASE:
        raise BackupError(
            f"the backup passphrase must be at least {MIN_PASSPHRASE} characters")


def is_encrypted(blob: bytes) -> bool:
    return blob[:len(MAGIC)] == MAGIC


def _public_header(manifest: dict) -> dict:
    """What stays readable without the passphrase. Enough to identify a file
    and decide whether to restore it; no email addresses, because the point
    of encrypting is that a leaked file gives up as little as possible."""
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "encrypted": True,
        "created_at": manifest.get("created_at"),
        "includes": manifest.get("includes", {}),
        "counts": manifest.get("counts", {}),
        "sensitive": manifest.get("sensitive", False),
    }


def encrypt_archive(zip_bytes: bytes, passphrase: str, manifest: dict) -> bytes:
    check_passphrase(passphrase)
    AESGCM = _aesgcm()
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(passphrase, salt)

    header = _public_header(manifest)
    header["kdf"] = {"name": "scrypt", "n": _KDF_N, "r": _KDF_R, "p": _KDF_P,
                     "salt": salt.hex()}
    header["nonce"] = nonce.hex()
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")

    # The header is the associated data, so editing it (to claim a backup
    # holds something it does not, say) breaks decryption rather than
    # passing quietly.
    ct = AESGCM(key).encrypt(nonce, zip_bytes, header_bytes)
    return (MAGIC + len(header_bytes).to_bytes(4, "big") + header_bytes + ct)


def read_envelope(blob: bytes) -> dict:
    """The plaintext header of an encrypted archive. No passphrase needed."""
    if not is_encrypted(blob):
        raise BackupError("that file is not an encrypted GroundedOps backup")
    try:
        off = len(MAGIC)
        hlen = int.from_bytes(blob[off:off + 4], "big")
        if hlen <= 0 or hlen > 64 * 1024:
            raise ValueError("implausible header length")
        header = json.loads(blob[off + 4:off + 4 + hlen])
    except Exception:
        raise BackupError("that backup's header is unreadable — the file may "
                          "be truncated or corrupt")
    if header.get("format") != FORMAT:
        raise BackupError("that archive was not produced by GroundedOps")
    if int(header.get("format_version", 0)) > FORMAT_VERSION:
        raise BackupError(
            f"that archive is format v{header.get('format_version')}, and this "
            f"install understands up to v{FORMAT_VERSION}. Upgrade first.")
    return header


def decrypt_archive(blob: bytes, passphrase: str) -> bytes:
    header = read_envelope(blob)
    if not passphrase:
        raise BackupError("that backup is encrypted — its passphrase is required")
    AESGCM = _aesgcm()
    off = len(MAGIC)
    hlen = int.from_bytes(blob[off:off + 4], "big")
    header_bytes = blob[off + 4:off + 4 + hlen]
    ct = blob[off + 4 + hlen:]
    try:
        kdf = header["kdf"]
        key = _derive_key(passphrase, bytes.fromhex(kdf["salt"]))
        nonce = bytes.fromhex(header["nonce"])
    except Exception:
        raise BackupError("that backup's header is unreadable")
    try:
        return AESGCM(key).decrypt(nonce, ct, header_bytes)
    except Exception:
        # One message for a wrong passphrase AND for a tampered file. GCM
        # cannot tell them apart, and guessing would be worse than saying so.
        raise BackupError(
            "could not decrypt: wrong passphrase, or the file has been "
            "altered since it was made")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── what lives where ──────────────────────────────────────────────────
# Resolved at call time, not import, because the tests and Docker both set
# these paths through the environment.

def _here() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _stores(include_accounts: bool) -> dict:
    """archive name -> path on disk."""
    out = {
        "faq_store.json": os.getenv("FAQ_STORE_PATH", "faq_store.json"),
        "faq_gaps.json": os.getenv("FAQ_GAP_PATH", "faq_gaps.json"),
        "catalog_config.json": os.getenv("CATALOG_CONFIG", "catalog_config.json"),
        "widget_config.json": os.getenv("WIDGET_CONFIG_PATH", "widget_config.json"),
        "widget_leads.json": os.getenv("WIDGET_LEADS_PATH", "widget_leads.json"),
    }
    if include_accounts:
        # Root only. Password hashes and staff addresses.
        out["accounts.json"] = os.getenv("ACCOUNTS_PATH", "accounts.json")
        out["policy.json"] = os.getenv("POLICY_PATH", "policy.json")
    return out


def _convo_db() -> str:
    return os.getenv("CONVO_DB_PATH", "conversations.db")


def _index_dir() -> str:
    return os.getenv("CHROMA_DIR", "./chroma_db")


def _documents_dir() -> str:
    explicit = (os.getenv("SOURCE_FILE_DIR") or "").strip()
    if explicit:
        return explicit
    return os.path.join(os.path.dirname(_here()), "documents")


# ── export ────────────────────────────────────────────────────────────

def _add_tree(zf: zipfile.ZipFile, src_dir: str, prefix: str) -> int:
    if not os.path.isdir(src_dir):
        return 0
    n = 0
    for root, _dirs, files in os.walk(src_dir):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, src_dir).replace("\\", "/")
            try:
                zf.write(full, f"{prefix}{rel}")
                n += 1
            except (OSError, ValueError) as e:
                # One unreadable file must not lose the whole backup.
                logger.warning(f"backup: skipped {full} ({e})")
    return n


def create_archive(created_by: str = "", level: str = "support",
                   include_documents: bool = True,
                   include_index: bool = True,
                   passphrase: str | None = None) -> tuple[bytes, dict]:
    """Build the archive in memory and return (bytes, manifest).

    In memory because these are ~100MB and a support tool that leaves
    half-written temp files around after a failure is its own problem. If
    archives ever outgrow that, stream to a temp file and clean up in a
    finally.
    """
    include_accounts = (level == "root")
    counts = {}
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        n = 0
        for arc_name, path in _stores(include_accounts).items():
            if os.path.isfile(path):
                zf.write(path, f"stores/{arc_name}")
                n += 1
        counts["stores"] = n

        convo = _convo_db()
        counts["conversations_db"] = 0
        if os.path.isfile(convo):
            zf.write(convo, "db/conversations.db")
            counts["conversations_db"] = 1

        counts["index_files"] = (
            _add_tree(zf, _index_dir(), "index/") if include_index else 0)
        counts["documents"] = (
            _add_tree(zf, _documents_dir(), "documents/") if include_documents else 0)

        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "created_at": _now(),
            "created_by": created_by,
            "created_by_level": level,
            "includes": {
                "accounts": include_accounts,
                "documents": bool(include_documents),
                "index": bool(include_index),
            },
            "counts": counts,
            # Read by the console before it will offer to restore.
            "sensitive": include_accounts,
            "note": ("Contains staff password hashes and customer contact "
                     "details. Store it like a password database."
                     if include_accounts else
                     "Contains customer contact details from the enquiry "
                     "forms. Store it somewhere access-controlled."),
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    data = buf.getvalue()
    if passphrase:
        data = encrypt_archive(data, passphrase, manifest)
        manifest = dict(manifest, encrypted=True)
    else:
        manifest = dict(manifest, encrypted=False)

    logger.info(f"backup exported by {created_by or 'unknown'} ({level}): "
                f"{counts} encrypted={bool(passphrase)}")
    return data, manifest


def suggested_filename(manifest: dict) -> str:
    stamp = (manifest.get("created_at") or "").replace(":", "").replace("-", "")
    stamp = stamp.replace("+0000", "Z").replace("T", "-")[:15]
    kind = "full" if manifest.get("includes", {}).get("accounts") else "content"
    # .gobk rather than .zip when sealed: an encrypted envelope is not a zip,
    # and naming it one just invites someone to try to open it and conclude
    # the backup is corrupt.
    ext = ENVELOPE_EXT if manifest.get("encrypted") else ".zip"
    return f"groundedops-{kind}-backup-{stamp or 'export'}{ext}"


# ── reading an archive safely ─────────────────────────────────────────

def _safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Members that are safe to extract.

    Rejects the zip-slip family outright: absolute paths, drive letters,
    anything containing '..', and symlinks. A zip is untrusted input as soon
    as someone can upload one, and the whole trick is a member named
    '../../etc/something' that escapes the directory you extract into.
    Checked by NAME here and again by resolved path at write time.
    """
    out = []
    total = 0
    for m in zf.infolist():
        if m.is_dir():
            continue
        name = m.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/") or ":" in name:
            raise BackupError(f"archive contains an unsafe path: {m.filename!r}")
        # Symlinks are stored in the high bits of external_attr. Extracting
        # one lets a later member write through it to anywhere on disk.
        if (m.external_attr >> 16) & 0o170000 == 0o120000:
            raise BackupError(f"archive contains a symlink: {m.filename!r}")
        total += m.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise BackupError(
                f"archive expands to more than this install allows "
                f"({MAX_UNCOMPRESSED_BYTES} bytes). Raise BACKUP_MAX_BYTES if "
                f"this is a genuine backup.")
        if name == "manifest.json" or name.startswith(_ALLOWED_PREFIXES):
            out.append(m)
    return out


def read_manifest(data: bytes, passphrase: str | None = None) -> dict:
    """Inspect an archive without writing anything, so the console can show
    what a file holds before anyone agrees to restore it.

    Handles both shapes. A plain zip still reads, because archives taken
    before encryption existed have to stay restorable.
    """
    if is_encrypted(data):
        data = decrypt_archive(data, passphrase or "")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            _safe_members(zf)          # validate before trusting the manifest
            raw = zf.read("manifest.json")
    except BackupError:
        raise
    except KeyError:
        raise BackupError("that file has no manifest.json — not a GroundedOps backup")
    except zipfile.BadZipFile:
        raise BackupError("that file is not a readable zip archive")

    try:
        manifest = json.loads(raw)
    except Exception:
        raise BackupError("the archive's manifest is unreadable")

    if manifest.get("format") != FORMAT:
        raise BackupError("that archive was not produced by GroundedOps")
    if int(manifest.get("format_version", 0)) > FORMAT_VERSION:
        raise BackupError(
            f"that archive is format v{manifest.get('format_version')}, and this "
            f"install understands up to v{FORMAT_VERSION}. Upgrade first.")
    return manifest


# ── restore ───────────────────────────────────────────────────────────

def _extract_to(zf: zipfile.ZipFile, member: zipfile.ZipInfo, dest: str) -> None:
    """Write one member to an exact destination, having already checked the
    name. The resolved-path check is the belt to _safe_members' braces."""
    dest_dir = os.path.dirname(os.path.abspath(dest))
    os.makedirs(dest_dir, exist_ok=True)
    if not os.path.abspath(dest).startswith(os.path.abspath(dest_dir)):
        raise BackupError(f"refusing to write outside the target: {dest!r}")
    with zf.open(member) as src, open(dest, "wb") as out:
        shutil.copyfileobj(src, out)


def restore_archive(data: bytes, restore_accounts: bool = False,
                    restore_documents: bool = True,
                    restore_index: bool = True,
                    restore_conversations: bool = True,
                    actor: str = "",
                    passphrase: str | None = None,
                    snapshot_passphrase: str | None = None) -> dict:
    """Overwrite this install's state from an archive.

    A safety snapshot is taken first, so the operator can get back to where
    they were. `restore_accounts` is off by default on purpose — see the
    module docstring.
    """
    if is_encrypted(data):
        # Decrypt once, up front. Everything below works on the inner zip.
        data = decrypt_archive(data, passphrase or "")
    manifest = read_manifest(data)

    # The pre-restore snapshot is sealed with the SAME passphrase by default:
    # it holds exactly what the archive being restored holds, so leaving it
    # in the clear on disk would undo the encryption for anyone who reached
    # the server. Pass snapshot_passphrase to use a different one.
    safety, _ = create_archive(created_by=f"auto-before-restore ({actor})",
                               level="root", include_documents=restore_documents,
                               include_index=False,
                               passphrase=snapshot_passphrase or passphrase)

    restored = {"stores": [], "documents": 0, "index": 0, "conversations": False}
    skipped = []

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = _safe_members(zf)
        store_targets = _stores(include_accounts=True)

        for m in members:
            name = m.filename.replace("\\", "/")

            if name.startswith("stores/"):
                base = name[len("stores/"):]
                if base in ("accounts.json", "policy.json") and not restore_accounts:
                    skipped.append(base)
                    continue
                target = store_targets.get(base)
                if not target:
                    skipped.append(base)
                    continue
                _extract_to(zf, m, target)
                restored["stores"].append(base)

            elif name.startswith("db/") and restore_conversations:
                if name.endswith("conversations.db"):
                    _extract_to(zf, m, _convo_db())
                    restored["conversations"] = True

            elif name.startswith("index/") and restore_index:
                rel = name[len("index/"):]
                _extract_to(zf, m, os.path.join(_index_dir(), *rel.split("/")))
                restored["index"] += 1

            elif name.startswith("documents/") and restore_documents:
                rel = name[len("documents/"):]
                _extract_to(zf, m, os.path.join(_documents_dir(), *rel.split("/")))
                restored["documents"] += 1

    logger.warning(f"backup restored by {actor or 'unknown'}: {restored}")
    return {
        "manifest": manifest,
        "restored": restored,
        "skipped": skipped,
        # Chroma holds an open sqlite connection to the index directory, so
        # files written under it are not picked up by the running process.
        "restart_required": restored["index"] > 0,
        "safety_snapshot": safety,
    }
