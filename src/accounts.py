"""Real admin accounts with levels, replacing the single shared password.

Three levels, deliberately few:

  root     — everything, including creating accounts and changing levels.
  support  — everything except account management. The day-to-day level.
  basic    — sign in and use the test chat. No admin surface at all.

`support` is the level that does actual work; `root` exists so that the
ability to grant access is itself a privilege someone has to hold. `basic`
is for people who need to try the assistant without being trusted with the
document store or the FAQ answers customers will be shown.

Sessions are stateless HMAC tokens, same construction as the widget tokens
in `quota.py` (payload b64url + '.' + HMAC-SHA256 b64url) so there is one
token idiom in this codebase rather than two. Nothing server-side is stored
per session, but a token is still revocable: it carries the account's
`token_epoch`, and bumping that (on password change, on disable) invalidates
every token already issued for that account.

Passwords are hashed with `hashlib.scrypt` — a real KDF, and in the standard
library, so this adds no dependency. That matters here because the `unit` CI
job installs an explicit package list rather than requirements.txt, so a new
dep is a second thing to remember.

── SSO seam ──
If company Microsoft/Entra sign-in is added later, it replaces exactly one
function: `verify_password`. An OIDC callback validates the ID token itself,
resolves the email claim to an account with `find_by_email`, and calls
`issue_session(user)` directly — no password involved. Everything
downstream (levels, guards, the console) is unchanged. Keep that seam: do
not let password checks leak into `issue_session` or the guards.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone

import keystore

logger = logging.getLogger(__name__)

_PATH = os.getenv("ACCOUNTS_PATH", "accounts.json")
_lock = threading.Lock()

LEVELS = ("basic", "support", "root")
_LEVEL_RANK = {"basic": 0, "support": 1, "root": 2}

# Accounts may only be created for company addresses. Set to "" to allow any
# domain (e.g. if an outside contractor genuinely needs an account).
ALLOWED_EMAIL_DOMAIN = os.getenv(
    "ALLOWED_EMAIL_DOMAIN", "innovative-technology.com").strip().lower()

SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", str(12 * 3600)))

# scrypt cost. n=2**14 keeps a single hash around 50-100ms on a normal
# machine: slow enough to make guessing expensive, fast enough that a login
# is not perceptibly delayed. Stored per-record so these can be raised later
# without invalidating existing passwords.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1


class AccountError(Exception):
    """Anything the caller should surface to a human as a 400."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session_secret() -> str:
    """Read through keystore so there is one place that knows where secrets
    come from. Separate from WIDGET_TOKEN_SECRET on purpose: a leaked widget
    secret should not also mint admin sessions."""
    return keystore.get_session_secret()


# ── persistence ───────────────────────────────────────────────────────

def _load() -> list[dict]:
    if os.path.exists(_PATH):
        try:
            with open(_PATH, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("users", []) if isinstance(data, dict) else data
        except Exception as e:
            logger.warning(f"accounts read failed: {e}")
    return []


def _save(users: list[dict]) -> None:
    """Written via a temp file and os.replace. Unlike the FAQ store, a
    half-written accounts file locks everyone out of the console, so this
    one is atomic."""
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"users": users}, f, indent=2)
    os.replace(tmp, _PATH)


# ── passwords ─────────────────────────────────────────────────────────

def _hash_password(password: str, salt: bytes | None = None) -> dict:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return {"algo": "scrypt", "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P,
            "salt": salt.hex(), "hash": dk.hex()}


def _check_password(password: str, rec: dict) -> bool:
    pw = rec.get("password") or {}
    if pw.get("algo") != "scrypt":
        return False
    try:
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(pw["salt"]),
            n=int(pw["n"]), r=int(pw["r"]), p=int(pw["p"]), dklen=32)
    except Exception as e:
        logger.warning(f"password check failed for account: {e}")
        return False
    return hmac.compare_digest(dk.hex(), pw.get("hash", ""))


MIN_PASSWORD_LENGTH = 12


def validate_password(password: str) -> None:
    """Length only. Composition rules (a digit, a symbol) push people toward
    'Password1!' and are worse than length for real strength."""
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AccountError(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def _validate_email(email: str) -> str:
    email = normalise_email(email)
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise AccountError("that is not a valid email address")
    if ALLOWED_EMAIL_DOMAIN and not email.endswith("@" + ALLOWED_EMAIL_DOMAIN):
        raise AccountError(
            f"accounts are limited to @{ALLOWED_EMAIL_DOMAIN} addresses")
    return email


# ── reads ─────────────────────────────────────────────────────────────

def _public(u: dict) -> dict:
    """An account as the console may see it — never the password record."""
    return {"id": u["id"], "email": u["email"], "name": u.get("name", ""),
            "level": u["level"], "disabled": bool(u.get("disabled")),
            "created_at": u.get("created_at", ""),
            "created_by": u.get("created_by", ""),
            "last_login": u.get("last_login", ""),
            "must_change_password": bool(u.get("must_change_password"))}


def list_users() -> list[dict]:
    return [_public(u) for u in sorted(_load(), key=lambda u: u["email"])]


def find_by_email(email: str) -> dict | None:
    email = normalise_email(email)
    for u in _load():
        if u["email"] == email:
            return u
    return None


def find_by_id(user_id: str) -> dict | None:
    for u in _load():
        if u["id"] == user_id:
            return u
    return None


def count_users() -> int:
    return len(_load())


def is_uninitialised() -> bool:
    """No accounts exist yet, so the console should offer to create the
    first root account instead of a sign-in form."""
    return count_users() == 0


def _active_roots(users: list[dict]) -> list[dict]:
    return [u for u in users
            if u["level"] == "root" and not u.get("disabled")]


# ── writes ────────────────────────────────────────────────────────────

def create_user(email: str, password: str, level: str, name: str = "",
                created_by: str = "", must_change_password: bool = True,
                _only_if_empty: bool = False) -> dict:
    """`_only_if_empty` is how bootstrap_root stays a single atomic check:
    the "are there no accounts yet" test has to happen under the same lock
    acquisition as the insert, or two simultaneous first-run requests can
    both pass it and create two root accounts."""
    if level not in LEVELS:
        raise AccountError(f"level must be one of {', '.join(LEVELS)}")
    email = _validate_email(email)
    validate_password(password)
    with _lock:
        users = _load()
        if _only_if_empty and users:
            raise AccountError("accounts already exist; ask a root user for access")
        if any(u["email"] == email for u in users):
            raise AccountError("an account with that email already exists")
        rec = {
            "id": uuid.uuid4().hex[:12],
            "email": email,
            "name": (name or "").strip(),
            "level": level,
            "password": _hash_password(password),
            "token_epoch": 1,
            "disabled": False,
            "created_at": _now(),
            "created_by": created_by,
            "last_login": "",
            "must_change_password": bool(must_change_password),
        }
        users.append(rec)
        _save(users)
    logger.info(f"account created: {email} ({level}) by {created_by or 'bootstrap'}")
    return _public(rec)


def bootstrap_root(email: str, password: str, name: str = "") -> dict:
    """Create the first root account. Refuses once any account exists, so
    the endpoint behind it cannot be used to add a second back door."""
    return create_user(email, password, "root", name=name,
                       created_by="bootstrap", must_change_password=False,
                       _only_if_empty=True)


def set_level(user_id: str, level: str, actor_id: str = "") -> dict:
    if level not in LEVELS:
        raise AccountError(f"level must be one of {', '.join(LEVELS)}")
    with _lock:
        users = _load()
        target = next((u for u in users if u["id"] == user_id), None)
        if not target:
            raise AccountError("no such account")
        # Demoting the last root leaves nobody able to manage accounts, and
        # nothing in this app can undo that from the outside.
        if target["level"] == "root" and level != "root":
            if len(_active_roots(users)) <= 1:
                raise AccountError("this is the only root account; promote "
                                   "someone else to root first")
        target["level"] = level
        _save(users)
    logger.info(f"account level changed: {target['email']} -> {level} by {actor_id}")
    return _public(target)


def set_disabled(user_id: str, disabled: bool, actor_id: str = "") -> dict:
    with _lock:
        users = _load()
        target = next((u for u in users if u["id"] == user_id), None)
        if not target:
            raise AccountError("no such account")
        if disabled and target["level"] == "root" and len(_active_roots(users)) <= 1:
            raise AccountError("this is the only root account; it cannot be disabled")
        if disabled and target["id"] == actor_id:
            raise AccountError("you cannot disable your own account")
        target["disabled"] = bool(disabled)
        # Existing sessions for a disabled account must stop working now,
        # not whenever their token happens to expire.
        target["token_epoch"] = int(target.get("token_epoch", 1)) + 1
        _save(users)
    logger.info(f"account {'disabled' if disabled else 'enabled'}: "
                f"{target['email']} by {actor_id}")
    return _public(target)


def delete_user(user_id: str, actor_id: str = "") -> None:
    with _lock:
        users = _load()
        target = next((u for u in users if u["id"] == user_id), None)
        if not target:
            raise AccountError("no such account")
        if target["id"] == actor_id:
            raise AccountError("you cannot delete your own account")
        if target["level"] == "root" and len(_active_roots(users)) <= 1:
            raise AccountError("this is the only root account; it cannot be deleted")
        users = [u for u in users if u["id"] != user_id]
        _save(users)
    logger.info(f"account deleted: {target['email']} by {actor_id}")


def set_password(user_id: str, new_password: str, actor_id: str = "",
                 must_change: bool = False) -> None:
    """Also bumps token_epoch, so changing a password signs out every other
    session that account had — which is the point of changing it."""
    validate_password(new_password)
    with _lock:
        users = _load()
        target = next((u for u in users if u["id"] == user_id), None)
        if not target:
            raise AccountError("no such account")
        target["password"] = _hash_password(new_password)
        target["token_epoch"] = int(target.get("token_epoch", 1)) + 1
        target["must_change_password"] = bool(must_change)
        _save(users)
    logger.info(f"password changed for {target['email']} by {actor_id or 'self'}")


# ── authentication ────────────────────────────────────────────────────

def verify_password(email: str, password: str) -> dict | None:
    """Returns the account record on success, None on any failure.

    Deliberately does not distinguish "no such account" from "wrong
    password" to the caller: that difference tells an attacker which
    company addresses have accounts.

    THIS is the function an SSO integration replaces — see the module
    docstring. Nothing else in this file knows about passwords.
    """
    email = normalise_email(email)
    user = find_by_email(email)
    if not user:
        # Spend roughly the same time as a real check so the response time
        # does not reveal whether the account exists.
        _hash_password(password or "")
        logger.info(f"sign-in refused (no such account): {email}")
        return None
    if user.get("disabled"):
        logger.info(f"sign-in refused (disabled): {email}")
        return None
    if not _check_password(password or "", user):
        logger.info(f"sign-in refused (bad password): {email}")
        return None
    return user


def record_login(user_id: str) -> None:
    with _lock:
        users = _load()
        target = next((u for u in users if u["id"] == user_id), None)
        if target:
            target["last_login"] = _now()
            _save(users)


# ── sessions ──────────────────────────────────────────────────────────

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_session(user: dict, ttl_seconds: int | None = None) -> str:
    """Mint a session token for an already-authenticated account.

    Takes an account record, not a password: this is the seam an SSO
    callback calls once it has validated the identity itself.
    """
    secret = _session_secret()
    if not secret:
        raise AccountError(
            "SESSION_SECRET is not set on the server; admin sign-in is "
            "disabled until it is")
    payload = {
        "uid": user["id"],
        "lvl": user["level"],
        "ep": int(user.get("token_epoch", 1)),
        "exp": int(time.time()) + (ttl_seconds or SESSION_TTL_SECONDS),
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    # CodeQL (py/weak-sensitive-data-hashing) flags this as hashing a
    # password with SHA256. It isn't: this is HMAC-SHA256 signing a session
    # TOKEN PAYLOAD (uid/level/exp — no password anywhere in `payload` or
    # `raw`), not storing or comparing a password. HMAC's security comes
    # from the secret key, not from the underlying hash being "expensive" —
    # that requirement is for unsalted password storage, which is what this
    # rule is actually meant to catch. The real password hashing is
    # hashlib.scrypt in _hash_password/_check_password above, unaffected.
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).digest()  # lgtm[py/weak-sensitive-data-hashing]
    return raw.decode() + "." + _b64e(sig)


def verify_session(token: str | None) -> dict | None:
    """Returns the current public account record, or None.

    The level is re-read from storage rather than trusted from the token, so
    a demotion takes effect on the demoted user's next request instead of
    when their token expires.
    """
    secret = _session_secret()
    if not token or not secret:
        return None
    try:
        raw_s, sig_s = token.split(".", 1)
        # Same false positive as issue_session above: HMAC-SHA256 verifying
        # a token signature, not password hashing.
        expected = hmac.new(secret.encode(), raw_s.encode(),
                            hashlib.sha256).digest()  # lgtm[py/weak-sensitive-data-hashing]
        if not hmac.compare_digest(expected, _b64d(sig_s)):
            logger.warning("admin session: bad signature")
            return None
        payload = json.loads(_b64d(raw_s))
        if int(payload.get("exp", 0)) < time.time():
            return None
        user = find_by_id(str(payload.get("uid", "")))
        if not user or user.get("disabled"):
            return None
        if int(payload.get("ep", 0)) != int(user.get("token_epoch", 1)):
            # Password changed or account disabled since this was issued.
            return None
        return _public(user)
    except Exception as e:
        logger.warning(f"admin session: rejected ({e})")
        return None


# ── authorisation ─────────────────────────────────────────────────────

def has_level(user: dict | None, minimum: str) -> bool:
    if not user:
        return False
    return _LEVEL_RANK.get(user.get("level", ""), -1) >= _LEVEL_RANK[minimum]
