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
If you have an existing logs.json, archive or delete it — nothing here
reads it.
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


def _trace_fields() -> dict:
    """M2: which conversation, who asked (visitor or test), how the turn
    ended and what checked the answer, read from the request's pipeline
    trace at write time. Every key is always present (None when there is no
    trace, e.g. a direct call from a script) so old and new rows sort into
    one 'missing' bucket rather than a schema split. Imported here, not in
    main, because tests stub logger and read main.query's source."""
    try:
        import pipeline_trace
        snap = pipeline_trace.snapshot() or {}
    except Exception:
        snap = {}
    meta = snap.get("meta") or {}
    exit_stage = snap.get("exit") or {}
    outcome = exit_stage.get("id")
    try:
        if outcome not in pipeline_trace.OUTCOMES:
            outcome = None     # a step along the way is not how it ended
    except Exception:
        outcome = None
    return {
        "session_id":       meta.get("session_id"),
        "origin":           meta.get("origin"),
        "surface":          meta.get("surface"),
        "outcome":          outcome,
        "verified_by":      meta.get("ground_via"),
        "verifier":         meta.get("verifier"),
        "service_degraded": meta.get("service_degraded"),
    }


def log_interaction(
    query: str,
    answer: str,
    role: str | None = None,
    model: str | None = None,
    sources: list[str] | None = None,
    grounding_score: float | None = None,
    flagged: bool = False,
    request_id: str | None = None,
    timing: dict[str, float] | None = None,
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
        "request_id":       request_id,
        "timing":           {k: round(float(v), 3)
                               for k, v in (timing or {}).items()},
    }
    entry.update(_trace_fields())

    line = json.dumps(entry)

    with _lock:
        try:
            _rotate_if_needed()
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except IOError as exc:
            _logger.error(f"Failed to write log: {exc}")


def log_feedback(request_id: str, vote: str, session_id: str | None = None) -> None:
    """8.10: a visitor's vote, as its own row joined to the answer's row by
    request_id. The vote itself is stored in widget_feedback.json; this
    file rotates, so it is only a mirror."""
    entry = {
        "timestamp":  datetime.utcnow().isoformat(),
        "event":      "feedback",
        "request_id": request_id,
        "session_id": session_id,
        "outcome":    "voted_" + vote,
    }
    with _lock:
        try:
            _rotate_if_needed()
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except IOError as exc:
            _logger.error(f"Failed to write log: {exc}")
