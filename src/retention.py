"""D-15: visitor text is deleted after RETENTION_DAYS (default 90).

Every store that holds what a visitor typed, or what we answered them, gets
a prune_before(cutoff) beside its own lock; this module calls each once a
day. Customer content (documents, FAQ, index), staff accounts and quota
counts are never touched. RETENTION_DAYS=0 turns deletion off.

Run once by hand:  python retention.py
"""
import hashlib
import hmac
import importlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 90
# Module that owns each store, in the order they are pruned.
STORES = ("logger", "conversations", "widget_api", "widget_config", "faq_store", "feedback_store")

# D-20261104-08: one line per erasure, beside logs.jsonl. It holds no visitor
# text (salted hashes and counts only), so prune() leaves it alone.
TOMBSTONE_FILE = "erasures.jsonl"
_tomb_lock = threading.Lock()


def _subject_hash(value: str) -> str | None:
    """A salted hash of an erased id or email: support can later confirm
    "chat X was erased" by hashing X again; the tombstone names nobody."""
    if not value:
        return None
    import keystore
    salt = keystore.get_session_secret() or "groundedops-erasure"
    return hmac.new(salt.encode(), b"erasure|" + value.encode(),
                    hashlib.sha256).hexdigest()[:32]


def _tombstone(source: str, email: str, session_ids, user_id: str, out: dict) -> None:
    import logger as interaction_log
    path = os.path.join(os.path.dirname(os.path.abspath(interaction_log.LOG_FILE)),
                        TOMBSTONE_FILE)
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "source": source,
           "sessions": [_subject_hash(s) for s in session_ids if s],
           "email": _subject_hash((email or "").strip().lower()),
           "user": _subject_hash(user_id),
           "removed": out}
    try:
        with _tomb_lock, open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as e:
        logger.warning(f"erasure: tombstone not written: {e}")


def days() -> int:
    """RETENTION_DAYS; 0 = off, missing or unreadable = 90."""
    try:
        return max(0, int(os.getenv("RETENTION_DAYS", str(DEFAULT_DAYS))))
    except ValueError:
        return DEFAULT_DAYS


def prune(days_: int | None = None, now: datetime | None = None) -> dict:
    """Delete visitor text older than `days_`. Returns rows removed per
    store; a store that failed reports its error instead, and the others
    still run."""
    days_ = days() if days_ is None else days_
    if days_ <= 0:
        return {}
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days_)
    out = {}
    for name in STORES:
        try:
            out[name] = importlib.import_module(name).prune_before(cutoff)
        except Exception as e:
            logger.warning(f"retention: {name} prune failed: {e}")
            out[name] = f"error: {e}"
    logger.info(f"retention: removed visitor text older than {days_} days: {out}")
    return out


def erase(email: str = "", session_ids=(), user_id: str = "", source: str = "console") -> dict:
    """UK GDPR erasure: delete one person's visitor text now instead of in
    90 days. An email finds their enquiries; their chats, votes and ratings are found
    by conversation (the widget's session id) or by the website's signed-in
    user id. The unanswered-questions list keeps question text without who
    asked it, so nothing there points back to a person; the 90-day rule
    still clears it. Returns rows removed per store.

    `source` is 'visitor' (the widget's own delete) or 'console'; every erasure
    leaves one tombstone line (see _tombstone). Legal hold is not applied:
    everything goes, until the CEO rules otherwise."""
    import stepwise
    chats = stepwise.with_parts(session_ids)
    jobs = (("widget_config", "erase_email", email),
            ("logger", "erase_sessions", chats),
            ("widget_api", "erase_sessions", chats),
            ("feedback_store", "erase_sessions", chats),
            ("conversations", "erase_user", user_id))
    out = {}
    for mod, fn, arg in jobs:
        if not arg:
            continue
        try:
            out[mod] = getattr(importlib.import_module(mod), fn)(arg)
        except Exception as e:
            logger.warning(f"erasure: {mod} failed: {e}")
            out[mod] = f"error: {e}"
    if out:
        _tombstone(source, email, session_ids, user_id, out)
    logger.info(f"erasure ({source}): removed {out}")
    return out


def _loop() -> None:
    time.sleep(300)   # let startup and warmup finish first
    while days() > 0:
        prune()
        time.sleep(24 * 3600)


def start() -> None:
    if days() <= 0:
        logger.info("visitor text retention disabled (RETENTION_DAYS=0)")
        return
    threading.Thread(target=_loop, name="retention", daemon=True).start()


if __name__ == "__main__":
    # The server runs from src/ with .env loaded (main.py); do the same so
    # the cwd-relative logs and the .env store paths resolve alike.
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)
    if os.path.exists(".env"):
        for line in open(".env", encoding="utf-8-sig"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    logging.basicConfig(level=logging.INFO)
    print(prune())
