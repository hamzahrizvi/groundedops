"""D-15: visitor text is deleted after RETENTION_DAYS (default 90).

Every store that holds what a visitor typed, or what we answered them, gets
a prune_before(cutoff) beside its own lock; this module calls each once a
day. Customer content (documents, FAQ, index), staff accounts and quota
counts are never touched. RETENTION_DAYS=0 turns deletion off.

Run once by hand:  python retention.py
"""
import importlib
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 90
# Module that owns each store, in the order they are pruned.
STORES = ("logger", "conversations", "widget_api", "widget_config", "faq_store")


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
