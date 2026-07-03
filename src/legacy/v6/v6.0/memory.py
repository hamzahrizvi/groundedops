"""
Session-scoped conversation memory.

BUG FIXED: memory used to be a single global, process-wide list, shared
across EVERY request regardless of which conversation made it. A query
from an entirely unrelated prior conversation — or a different script
run hitting the same long-running server — could silently leak into
"follow-up" query resolution for a completely different conversation.
This was the root cause of self-contained queries like "how to connect
tablet to hub" returning content for whatever unrelated query happened
to run immediately before it.

Memory is now keyed by session_id, supplied by the caller. The Streamlit
app generates one persistent UUID per browser session; test_queries.py
generates one per script run. Calls that omit session_id fall back to a
shared "default" bucket — fine for quick manual testing, but every real
caller should pass its own id.
"""

import threading
import time

MAX_MEMORY = 6
MAX_ANSWER_LEN = 300
SESSION_TTL_SECONDS = 4 * 60 * 60   # inactive sessions are reaped after 4h

_sessions: dict[str, list[dict]] = {}
_last_seen: dict[str, float] = {}
_lock = threading.Lock()


def _reap_expired_locked() -> None:
    """Must be called while holding _lock. Cheap opportunistic cleanup —
    runs on every write rather than via a background thread, since this
    project's scale doesn't justify one."""
    now = time.time()
    expired = [sid for sid, t in _last_seen.items() if now - t > SESSION_TTL_SECONDS]
    for sid in expired:
        _sessions.pop(sid, None)
        _last_seen.pop(sid, None)


def add_to_memory(session_id: str, query: str, answer: str) -> None:
    lower = answer.lower()
    if "could not find" in lower or "unable to generate" in lower:
        return

    with _lock:
        _reap_expired_locked()
        _last_seen[session_id] = time.time()
        history = _sessions.setdefault(session_id, [])
        history.append({"q": query, "a": answer[:MAX_ANSWER_LEN]})
        if len(history) > MAX_MEMORY:
            history.pop(0)


def get_history(session_id: str) -> list[dict]:
    """Raw {q, a} turns for this session, oldest first. Used to build the
    query-condensation prompt (see llm.condense_query)."""
    with _lock:
        return list(_sessions.get(session_id, []))


def get_last_query(session_id: str) -> str | None:
    history = get_history(session_id)
    return history[-1]["q"] if history else None


def clear_memory(session_id: str | None = None) -> None:
    """Clear one session's history, or every session if session_id is
    omitted (used by the full knowledge-base /reset)."""
    with _lock:
        if session_id is None:
            _sessions.clear()
            _last_seen.clear()
        else:
            _sessions.pop(session_id, None)
            _last_seen.pop(session_id, None)
