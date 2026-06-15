"""
Interaction logger — JSONL, append-only.

Previously this read the entire logs.json array, appended one entry,
and rewrote the whole file on every request — O(n) per write, and with
no locking, concurrent requests (now real: main.py uses a background
warmup thread + FastAPI's threadpool for sync routes) could interleave
writes and corrupt the file.

This version:
  - appends one JSON object per line (JSONL)        → O(1) per write
  - holds a threading.Lock around every read/write   → no interleaving
  - rotates the file once it exceeds MAX_LOG_BYTES   → bounded growth
  - truncates long answers at a sentence boundary    → no mid-word cuts
  - stores a normalized query field                  → easy dedup/analytics

NOTE: this is a different file format from the old logs.json (JSON array).
If you have an existing logs.json, archive or delete it — it won't be
read by get_last_logs()/get_flagged_logs() here.
"""

import json
import logging
import os
import threading
from datetime import datetime

LOG_FILE      = "logs.jsonl"
MAX_LOG_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_ANSWER_LEN = 500

_logger = logging.getLogger(__name__)
_lock   = threading.Lock()


def _truncate_answer(answer: str, max_len: int = MAX_ANSWER_LEN) -> str:
    """Truncate at the last sentence boundary before max_len, if one exists
    in the back half of the string; otherwise hard-truncate with an ellipsis.

    NOTE: `cut` only represents a real sentence boundary if it's strictly
    shorter than `truncated` (i.e. ". " was actually found). If no ". "
    exists, rsplit returns the string unchanged (cut == truncated), which
    is always > max_len*0.5 — without the length check below, that case
    would incorrectly take the "looks like a complete sentence" branch
    and append "." to an arbitrarily-truncated string, hiding the cut.
    """
    if len(answer) <= max_len:
        return answer

    truncated = answer[:max_len]
    cut = truncated.rsplit(". ", 1)[0]

    if len(cut) < len(truncated) and len(cut) > max_len * 0.5:
        return cut + "."

    return truncated.rstrip() + "…"


def _rotate_if_needed() -> None:
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        archive = f"logs_{ts}.jsonl"
        try:
            os.rename(LOG_FILE, archive)
            _logger.info(f"Rotated log file → {archive}")
        except OSError as exc:
            _logger.error(f"Log rotation failed: {exc}")


def log_interaction(
    query: str,
    answer: str,
    role: str | None = None,
    model: str | None = None,
    sources: list[str] | None = None,
    grounding_score: float | None = None,
    flagged: bool = False,
) -> None:
    entry = {
        "timestamp":        datetime.utcnow().isoformat(),
        "query":            query,
        "query_normalized": query.strip().lower(),
        "answer":           _truncate_answer(answer or ""),
        "role":             role,
        "model":            model,
        "sources":          sources or [],
        "grounding_score":  grounding_score,
        "flagged":          flagged,
    }

    line = json.dumps(entry)

    with _lock:
        try:
            _rotate_if_needed()
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except IOError as exc:
            _logger.error(f"Failed to write log: {exc}")


def _read_all_entries() -> list[dict]:
    if not os.path.exists(LOG_FILE):
        return []

    entries: list[dict] = []
    with _lock:
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except IOError:
            return []

    return entries


def get_last_logs(n: int = 5) -> list[dict]:
    return _read_all_entries()[-n:]


def get_flagged_logs() -> list[dict]:
    """Return all interactions where the grounding check failed."""
    return [e for e in _read_all_entries() if e.get("flagged")]