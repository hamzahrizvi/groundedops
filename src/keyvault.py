"""
Encrypted-at-rest storage for the DeepSeek API key.

Replaces the old plaintext .deepseek_key.json. The key is encrypted with
Fernet (AES-128-CBC + HMAC) before being written to disk, and is never
returned to the GUI for display — the GUI can only ask "is a key set?"
and set/clear it.

THREAT MODEL — read this before assuming more security than exists.
This is a LOCAL desktop tool that must decrypt the key automatically on
every launch, with no user passphrase prompt. That means the material
needed to decrypt necessarily lives on the same machine as the
ciphertext. So this protects against:
  - the key sitting in plaintext in a file (the previous behaviour),
  - accidental exposure via git commit / screen-share / file preview,
  - the key being visible/readable in the GUI after it's entered.
It does NOT protect against an attacker who already has read access to
this user's filesystem and can run this code — they can decrypt it the
same way the app does. If you need protection against that, the key
must not be stored at all (enter it per-session), or protected by a
user passphrase. This module deliberately does not overclaim.

The Fernet key is derived (PBKDF2-HMAC-SHA256) from a machine-stable
seed rather than stored as a separate adjacent "master key" file, so
the ciphertext isn't trivially portable to another machine and there's
no obvious plaintext key sitting next to it.
"""

import base64
import getpass
import hashlib
import os
import platform

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_VAULT_FILE = os.path.join(os.path.dirname(__file__), ".deepseek_key.enc")

# Fixed application salt. This is NOT a secret (it's in source) — its
# only job is domain-separation for the KDF so the derived key is
# specific to this app rather than reusable elsewhere.
_APP_SALT = b"groundedops-deepseek-keyvault-v1"


def _machine_seed() -> bytes:
    """A stable-per-machine-and-user seed for key derivation.

    Uses hostname + OS + username + home dir. Stable across launches on
    the same machine/account, and different on another machine — so the
    encrypted file isn't portable. Not a secret; see module threat model.
    """
    parts = [
        platform.node(),
        platform.system(),
        platform.machine(),
        _safe_getuser(),
        os.path.expanduser("~"),
    ]
    return "|".join(parts).encode("utf-8")


def _safe_getuser() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


def _fernet() -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_APP_SALT,
        iterations=200_000,
    )
    derived = kdf.derive(_machine_seed())
    return Fernet(base64.urlsafe_b64encode(derived))


def save_key(key: str) -> None:
    """Encrypt and persist the DeepSeek API key. Overwrites any existing."""
    if not key or not key.strip():
        raise ValueError("Refusing to save an empty key")
    token = _fernet().encrypt(key.strip().encode("utf-8"))
    with open(_VAULT_FILE, "wb") as f:
        f.write(token)
    # Best-effort restrictive perms (POSIX). No-op / ignored on Windows,
    # where NTFS ACLs already scope the file to the user profile.
    try:
        os.chmod(_VAULT_FILE, 0o600)
    except (OSError, NotImplementedError):
        pass


def load_key() -> str | None:
    """Decrypt and return the stored key, or None if none set / unreadable.

    Returns None (rather than raising) if the vault file is missing or
    can't be decrypted — e.g. it was copied from another machine, so the
    machine-derived key no longer matches. The caller treats "no usable
    key" uniformly; a stale file just means the user must re-enter.
    """
    if not os.path.exists(_VAULT_FILE):
        return None
    try:
        with open(_VAULT_FILE, "rb") as f:
            token = f.read()
        return _fernet().decrypt(token).decode("utf-8")
    except (InvalidToken, ValueError, OSError):
        return None


def has_key() -> bool:
    """True if a usable (decryptable) key is stored. Never returns the key."""
    return load_key() is not None


def clear_key() -> None:
    if os.path.exists(_VAULT_FILE):
        os.remove(_VAULT_FILE)


def migrate_legacy_plaintext(legacy_path: str = ".deepseek_key.json") -> bool:
    """One-time migration: if an old plaintext .deepseek_key.json exists,
    re-encrypt its key into the vault and delete the plaintext file.

    Returns True if a migration happened. Safe to call on every startup.
    """
    abs_legacy = legacy_path
    if not os.path.isabs(abs_legacy):
        abs_legacy = os.path.join(os.path.dirname(__file__), legacy_path)
    if not os.path.exists(abs_legacy):
        return False
    try:
        import json
        with open(abs_legacy, "r", encoding="utf-8") as f:
            legacy_key = json.load(f).get("key", "")
        if legacy_key.strip():
            save_key(legacy_key)
        os.remove(abs_legacy)  # remove plaintext regardless
        return bool(legacy_key.strip())
    except Exception:
        return False
