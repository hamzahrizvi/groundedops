"""Startup state shared by main.py's warmup and the routers.

APP_STATE is what /status and /health report and what capability() reads to
decide whether an endpoint may be used yet. It is ONE dict on purpose: main
re-exports the same object, and the tests flip main.APP_STATE["ready"].
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)


# The startup stages, in the order _warmup_stack runs them.
_WARMUP_STAGES = (
    ("database",   "Document database"),
    ("embeddings", "Search model"),
    ("reranker",   "Ranking model"),
    ("grounding",  "Grounding model"),
    ("faq",        "FAQ search"),
    ("specs",      "Product specifications"),
)


# What each capability needs before it can honestly be offered.
#
# WHY THIS IS NOT ONE "ready" FLAG. It was, and everything -- upload, query,
# the whole console -- waited behind the SLOWEST stage. That was the spec
# index: two minutes of PDF table extraction that only cross-product spec
# questions use. Filing a document or editing an FAQ needs none of it.
#
# The console greys a page until its capability is true and the endpoints
# below refuse on exactly the same condition, so what the UI offers and what
# the server accepts cannot drift apart.
_CAPABILITY_NEEDS = {
    "search": ("database", "embeddings"),
    "ask":    ("database", "embeddings", "reranker", "grounding"),
    "faq":    ("faq",),
    "specs":  ("specs",),
}


APP_STATE = {
    # Everything warm. Means exactly what it meant before, because /health
    # reports it and an unattended assessment reads that.
    "ready": False,
    "progress": 0,
    "message": "Starting",
    "error": None,
    # Per-stage detail, so the console can say WHAT it is waiting for
    # instead of showing a bar that means nothing to the person watching it.
    "stages": [{"key": k, "label": l, "state": "pending", "seconds": None}
               for k, l in _WARMUP_STAGES],
    "capabilities": {c: False for c in _CAPABILITY_NEEDS},
}


APP_STATE_LOCK = threading.Lock()


def _set_app_state(*, ready=None, progress=None, message=None, error=None):
    with APP_STATE_LOCK:
        if ready is not None:
            APP_STATE["ready"] = ready
        if progress is not None:
            APP_STATE["progress"] = progress
        if message is not None:
            APP_STATE["message"] = message
        if error is not None:
            APP_STATE["error"] = error


def _recompute_capabilities() -> None:
    """Caller holds APP_STATE_LOCK."""
    done = {st["key"] for st in APP_STATE["stages"] if st["state"] == "done"}
    for cap, needs in _CAPABILITY_NEEDS.items():
        APP_STATE["capabilities"][cap] = all(n in done for n in needs)


def _set_stage(key: str, state: str, seconds=None, error=None) -> None:
    with APP_STATE_LOCK:
        for st in APP_STATE["stages"]:
            if st["key"] == key:
                st["state"] = state
                if seconds is not None:
                    st["seconds"] = round(seconds, 1)
                if error:
                    st["error"] = error
                break
        _recompute_capabilities()
        settled = sum(1 for st in APP_STATE["stages"]
                      if st["state"] in ("done", "error"))
        APP_STATE["progress"] = int(settled * 100 / len(APP_STATE["stages"]))


def capability(name: str) -> bool:
    """Whether a capability is up. Endpoints gate on this rather than on
    `ready`, so one slow stage does not hold back work that does not need
    it."""
    with APP_STATE_LOCK:
        # `ready` implies all of them -- it is only set once every stage is
        # done -- and stating that here keeps the two from disagreeing for
        # anything that flips the flag directly, the test harness included.
        return bool(APP_STATE["ready"]
                    or APP_STATE["capabilities"].get(name))


def _run_stage(key: str, label: str, fn) -> bool:
    """Run one warmup stage, recording how it went.

    A stage that fails no longer aborts the ones after it. The reranker
    failing used to leave the FAQ cache and the spec index unbuilt as well,
    so one missing model took out every capability instead of the one it
    belongs to.
    """
    _set_app_state(message="Loading " + label.lower())
    _set_stage(key, "working")
    t0 = time.perf_counter()
    try:
        fn()
    except Exception as exc:
        logger.exception("startup stage '%s' failed" % key)
        _set_stage(key, "error", time.perf_counter() - t0, str(exc))
        _set_app_state(error="%s: %s" % (label, exc))
        return False
    took = time.perf_counter() - t0
    _set_stage(key, "done", took)
    logger.info("warmup: %s ready in %.1fs", label, took)
    return True
