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
