"""FAQ store (v10.4): the doc2query questions generated at ingest, shown
per product on the FAQ page, with admin-editable answers.

Each entry: {id, source, products, question, answer, edited}. The default
answer is the source chunk the question was generated from; an admin can
replace it with a clean curated answer. Persisted to faq_store.json.

This also becomes the seed for the future FAQ semantic cache (Phase 2):
curated Q/A pairs are exactly what that cache needs.
"""
import json
import os
import threading
import uuid
import logging

logger = logging.getLogger(__name__)

_PATH = os.getenv("FAQ_STORE_PATH", "faq_store.json")
_lock = threading.Lock()


def _load() -> list[dict]:
    if os.path.exists(_PATH):
        try:
            with open(_PATH) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"faq store read failed: {e}")
    return []


def _save(items: list[dict]) -> None:
    with open(_PATH, "w") as f:
        json.dump(items, f, indent=2)


def record_questions(source: str, products: str, qa_pairs: list[dict],
                     category: str = "") -> None:
    """Called at ingest. Replaces any existing entries for this source
    (re-ingest refreshes them) but PRESERVES admin edits by question text.
    Stores both product and category so FAQ lookups by either key work
    (a category-level chat asks for the category's questions)."""
    with _lock:
        items = _load()
        edited = {(it["source"], it["question"]): it["answer"]
                  for it in items if it.get("edited") and it["source"] == source}
        items = [it for it in items if it["source"] != source]
        for qa in qa_pairs:
            key = (source, qa["question"])
            items.append({
                "id": str(uuid.uuid4()),
                "source": source,
                "products": products,
                "category": category,
                "question": qa["question"],
                "answer": edited.get(key, qa["answer"]),
                "edited": key in edited,
            })
        _save(items)


def list_for_product(scope_key: str | None) -> list[dict]:
    """Match by PRODUCT or CATEGORY key (v10.6) — a category-level chat
    passes the category key, a product chat passes the product key."""
    items = _load()
    if not scope_key or scope_key == "all":
        return items
    out = []
    for it in items:
        prods = (it.get("products") or "").split(",")
        if scope_key in prods or scope_key == it.get("category"):
            out.append(it)
    return out


def add_entry(question: str, answer: str = "", products: str = "",
              category: str = "", source: str = "") -> dict:
    """Add a hand-written Q/A (v13.0).

    Marked edited=True on creation, which is what protects it: both
    record_questions (re-ingest) and generate_for_source (re-drafting)
    preserve edited entries, so a hand-written answer is never silently
    replaced by a generated one. `source` defaults to a sentinel rather
    than a filename because there is no document behind this entry — and
    record_questions() deletes by source, so giving it a real filename
    would make a re-ingest of that document delete an answer a human
    wrote by hand.
    """
    q = (question or "").strip()
    if not q:
        raise ValueError("question is required")
    with _lock:
        items = _load()
        entry = {
            "id": str(uuid.uuid4()),
            "source": source or "__manual__",
            "products": products or "",
            "category": category or "",
            "question": q,
            "answer": (answer or "").strip(),
            "edited": True,
            "manual": True,
        }
        items.append(entry)
        _save(items)
        return entry


def update_answer(faq_id: str, answer: str) -> dict | None:
    with _lock:
        items = _load()
        for it in items:
            if it["id"] == faq_id:
                it["answer"] = answer
                it["edited"] = True
                _save(items)
                return it
    return None


def update_entry(faq_id: str, question: str | None = None,
                 answer: str | None = None) -> dict | None:
    """Edit the question text as well as the answer (v13.0).

    The console has always sent both — a drafted question is often as
    badly worded as its answer, and being able to fix only half of it
    made the review pass much less useful. Either field may be omitted
    to leave it unchanged.
    """
    with _lock:
        items = _load()
        for it in items:
            if it["id"] == faq_id:
                if question is not None and question.strip():
                    new_q = question.strip()
                    # Remember the wording this entry was GENERATED with,
                    # once, before the first edit overwrites it. Re-drafting
                    # compares against both (see merge_questions): the model
                    # will regenerate something close to its own original
                    # phrasing, not the admin's rewrite, so without this an
                    # edited question comes back as a near-duplicate on the
                    # next draft.
                    if new_q != it["question"] and "original_question" not in it:
                        it["original_question"] = it["question"]
                    it["question"] = new_q
                if answer is not None:
                    it["answer"] = answer
                it["edited"] = True
                _save(items)
                return it
    return None


def get_entry(faq_id: str) -> dict | None:
    return next((it for it in _load() if it["id"] == faq_id), None)


def delete_scope(scope_key: str | None) -> int:
    """Bulk-delete every entry for a product/category, or ALL of them
    when scope_key is falsy. Returns how many were removed.

    Destructive and irreversible — the caller (main.py) gates this behind
    an explicit confirm flag as well as the admin password, because the
    blast radius includes hand-written answers that exist nowhere else.
    """
    with _lock:
        items = _load()
        before = len(items)
        if not scope_key or scope_key == "all":
            items = []
        else:
            keep = []
            for it in items:
                prods = (it.get("products") or "").split(",")
                if scope_key in prods or scope_key == it.get("category"):
                    continue
                keep.append(it)
            items = keep
        _save(items)
        return before - len(items)


def merge_questions(source: str, products: str, qa_pairs: list[dict],
                    category: str = "") -> dict:
    """ADD generated Q/A for a source without discarding what is there
    (v13.0) — the drafting path used by /admin/faq/autogenerate.

    record_questions() replaces every entry for a source, which is right
    at ingest time (the document changed, its questions should be
    regenerated). It is wrong for re-drafting: an admin who has reviewed
    30 answers and clicks "Draft answers" again to pick up a few more
    should not have the un-edited 30 deleted and replaced with fresh
    un-reviewed text, losing the review work of anyone who had read them.

    So this MERGES: questions already present for the source are skipped
    (reported as duplicates), and only genuinely new ones are appended.
    Comparison is on normalized question text, so trivial case and
    punctuation differences don't produce near-duplicate pairs.
    """
    def _key(q: str) -> str:
        import re as _re
        return _re.sub(r"\s+", " ", _re.sub(r"[^\w\s]", " ", (q or "").lower())).strip()

    with _lock:
        items = _load()
        # Match against the CURRENT wording and, for entries an admin has
        # rewritten, the wording they were originally generated with.
        existing = set()
        for it in items:
            existing.add(_key(it["question"]))
            if it.get("original_question"):
                existing.add(_key(it["original_question"]))
        added, dupes = 0, 0
        for qa in qa_pairs:
            q = (qa.get("question") or "").strip()
            if not q:
                continue
            k = _key(q)
            if k in existing:
                dupes += 1
                continue
            existing.add(k)
            items.append({
                "id": str(uuid.uuid4()),
                "source": source,
                "products": products or "",
                "category": category or "",
                "question": q,
                "answer": (qa.get("answer") or "").strip(),
                "edited": False,
                "manual": False,
            })
            added += 1
        if added:
            _save(items)
        return {"added": added, "skipped_duplicates": dupes}


def delete_entry(faq_id: str) -> bool:
    with _lock:
        items = _load()
        n = len(items)
        items = [it for it in items if it["id"] != faq_id]
        _save(items)
        return len(items) < n


def _tokens(text: str) -> set:
    import re
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def match_answer(question: str, scope_key: str | None = None,
                 min_overlap: float = 0.6) -> dict | None:
    """v10.14: find a saved FAQ whose question closely matches `question`,
    returning its curated answer. Lexical Jaccard over word tokens — no
    embedding dependency. Only matches when overlap is high AND the FAQ
    has a non-empty answer. Used to serve curated answers for repeat
    questions before hitting the full RAG pipeline."""
    q_tokens = _tokens(question)
    if not q_tokens:
        return None
    best, best_score = None, 0.0
    for it in list_for_product(scope_key):
        if not (it.get("answer") or "").strip():
            continue
        f_tokens = _tokens(it["question"])
        if not f_tokens:
            continue
        inter = len(q_tokens & f_tokens)
        union = len(q_tokens | f_tokens)
        score = inter / union if union else 0.0
        if score > best_score:
            best, best_score = it, score
    if best and best_score >= min_overlap:
        return {"answer": best["answer"], "question": best["question"], "score": round(best_score, 3)}
    return None
