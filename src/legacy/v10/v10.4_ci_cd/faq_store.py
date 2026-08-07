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


def record_questions(source: str, products: str, qa_pairs: list[dict]) -> None:
    """Called at ingest. Replaces any existing entries for this source
    (re-ingest refreshes them) but PRESERVES admin edits by question text."""
    with _lock:
        items = _load()
        # keep edited answers keyed by (source, question)
        edited = {(it["source"], it["question"]): it["answer"]
                  for it in items if it.get("edited") and it["source"] == source}
        items = [it for it in items if it["source"] != source]
        for qa in qa_pairs:
            key = (source, qa["question"])
            items.append({
                "id": str(uuid.uuid4()),
                "source": source,
                "products": products,
                "question": qa["question"],
                "answer": edited.get(key, qa["answer"]),
                "edited": key in edited,
            })
        _save(items)


def list_for_product(product_key: str | None) -> list[dict]:
    items = _load()
    if not product_key or product_key == "all":
        return items
    return [it for it in items if product_key in (it.get("products") or "").split(",")]


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
