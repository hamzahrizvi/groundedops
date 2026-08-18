"""Answer gaps (v13.0): the questions real visitors asked that the
reviewed answers did not cover.

The console's Overview has always claimed to show "questions people could
not get answered", and the Answers page has a "People asked, we could not
answer" panel — but nothing was ever recording them. This module is that
recorder.

WHAT COUNTS AS A GAP
--------------------
Only a query that ended in a genuine miss:

  role="rejected"   the retrieval gate found nothing relevant, or the
                    grounding check suppressed an answer we could not
                    verify. Either way the visitor got the flat "I could
                    not find that in the knowledge base."

Deliberately NOT recorded:

  role="clarify"    the widget asked which product/section was meant.
                    That is a working conversation, not a gap — logging
                    it would bury the real misses under every ambiguous
                    first turn.
  from_faq hits     answered from a curated answer.
  answered/extract  answered from the documents.

DEDUPLICATION
-------------
Visitors ask the same thing a dozen ways ("does it need wifi", "Does it
need WiFi?", "does it need wi-fi"). Counting those as twelve separate
gaps makes the list useless — the whole point of ordering by frequency is
to surface what is actually most asked. Gaps are therefore keyed by a
normalized form (lowercased, punctuation stripped, whitespace collapsed)
and the FIRST-SEEN spelling is kept as the display text, since that is
real visitor phrasing rather than a mangled key.

This is deliberately lexical, not semantic. An embedding-based clustering
of near-duplicate questions would group "does it need wifi" with "is an
internet connection required", which the normalized key does not. That is
a real limitation and the right next step, but it needs an embedding call
on the write path of every failed query — a cost that should be measured
before it is imposed. The lexical key is exact, free, and already
collapses the case/punctuation variants that dominate in practice.

RESOLUTION
----------
A gap is not deleted when it is answered — it is marked resolved, with
the FAQ id that answered it. Deleting would lose the "asked 14x" evidence
that justified writing the answer in the first place, and would let the
same question silently re-accumulate as if it were new. Resolved gaps
drop out of the default list but stay in the file.

Persisted to gaps_store.json (override with GAPS_STORE_PATH), matching
faq_store / catalog / conversations: a small JSON file under a lock, no
new infrastructure to deploy.
"""
import json
import os
import re
import threading
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_PATH = os.getenv("GAPS_STORE_PATH", "gaps_store.json")
_lock = threading.Lock()

# Cap on stored gaps. A public widget that starts getting scraped or spammed
# would otherwise grow this file without bound. When the cap is hit the
# least-asked, oldest entries are dropped first — the top of the list (the
# frequently-asked misses that are the entire point) is never evicted.
MAX_GAPS = int(os.getenv("MAX_GAPS", "2000"))

# Questions shorter than this are almost always noise ("?", "hi", "test")
# rather than a real unmet information need.
MIN_QUESTION_CHARS = 8


def _load() -> list[dict]:
    if os.path.exists(_PATH):
        try:
            with open(_PATH) as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception as e:
            logger.warning(f"gaps store read failed: {e}")
    return []


def _save(items: list[dict]) -> None:
    try:
        with open(_PATH, "w") as f:
            json.dump(items, f, indent=2)
    except Exception as e:
        # Never let a gap-recording failure break a query response.
        logger.warning(f"gaps store write failed: {e}")


def _norm(q: str) -> str:
    """Normalized dedup key: lowercase, punctuation stripped, whitespace
    collapsed. See the DEDUPLICATION note in the module docstring for why
    this is lexical rather than semantic."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (q or "").lower())).strip()


def record(question: str, product: str | None = None,
           category: str | None = None, reason: str = "") -> None:
    """Log an unanswered question. Called from /query on a genuine miss.

    Swallows its own errors by design: this runs on the response path of a
    user-facing query, and a failure to record analytics must never turn a
    served answer into a 500.
    """
    try:
        q = (question or "").strip()
        if len(q) < MIN_QUESTION_CHARS:
            return
        key = _norm(q)
        if not key:
            return
        now = datetime.now(timezone.utc).isoformat()
        with _lock:
            items = _load()
            for it in items:
                if it.get("key") == key and not it.get("resolved"):
                    it["times_asked"] = int(it.get("times_asked", 1)) + 1
                    it["last_asked"] = now
                    # Keep the most specific scope we have seen for this
                    # question — a gap first asked with no product selected
                    # and later asked inside a product belongs to that
                    # product, which is what the grouped view needs.
                    if product and not it.get("product"):
                        it["product"] = product
                    if category and not it.get("category"):
                        it["category"] = category
                    _save(items)
                    return
            items.append({
                "id": key,                 # the normalized form IS the id
                "key": key,
                "question": q,             # first-seen spelling, as asked
                "product": product or "",
                "category": category or "",
                "reason": reason,
                "times_asked": 1,
                "first_asked": now,
                "last_asked": now,
                "resolved": False,
                "resolved_faq_id": None,
            })
            if len(items) > MAX_GAPS:
                # Evict least-asked first, oldest-first within that.
                items.sort(key=lambda g: (int(g.get("times_asked", 1)),
                                          g.get("last_asked", "")))
                items = items[len(items) - MAX_GAPS:]
            _save(items)
    except Exception as e:
        logger.warning(f"gap record failed (non-fatal): {e}")


def list_gaps(product: str | None = None, include_resolved: bool = False,
              limit: int = 200) -> list[dict]:
    """Most-asked first — the order the console presents as a to-do list."""
    items = _load()
    if not include_resolved:
        items = [g for g in items if not g.get("resolved")]
    if product:
        items = [g for g in items
                 if g.get("product") == product or g.get("category") == product]
    items.sort(key=lambda g: (-int(g.get("times_asked", 1)),
                              g.get("last_asked", "")), reverse=False)
    return items[:limit]


def resolve(gap_id: str, faq_id: str | None = None) -> bool:
    """Mark a gap answered. Kept, not deleted — see the module docstring."""
    with _lock:
        items = _load()
        for it in items:
            if it.get("id") == gap_id or it.get("key") == gap_id:
                it["resolved"] = True
                it["resolved_faq_id"] = faq_id
                it["resolved_at"] = datetime.now(timezone.utc).isoformat()
                _save(items)
                return True
    return False


def resolve_matching(question: str, faq_id: str | None = None) -> int:
    """Resolve any gap whose normalized form matches `question`.

    Called when an admin saves a new curated answer, so that answering a
    question from the gaps list clears it without a second explicit
    action. Returns how many were resolved (0 or 1 in practice).
    """
    key = _norm(question)
    if not key:
        return 0
    with _lock:
        items = _load()
        n = 0
        for it in items:
            if it.get("key") == key and not it.get("resolved"):
                it["resolved"] = True
                it["resolved_faq_id"] = faq_id
                it["resolved_at"] = datetime.now(timezone.utc).isoformat()
                n += 1
        if n:
            _save(items)
        return n


def delete(gap_id: str) -> bool:
    """Hard-delete a gap — for spam/noise that should not be in the list."""
    with _lock:
        items = _load()
        n = len(items)
        items = [g for g in items if g.get("id") != gap_id and g.get("key") != gap_id]
        _save(items)
        return len(items) < n


def stats() -> dict:
    items = _load()
    open_gaps = [g for g in items if not g.get("resolved")]
    return {
        "open": len(open_gaps),
        "resolved": len(items) - len(open_gaps),
        "total_asks": sum(int(g.get("times_asked", 1)) for g in open_gaps),
    }
