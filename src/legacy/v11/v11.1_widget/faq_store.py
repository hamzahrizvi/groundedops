"""FAQ store (v10.4): admin-curated Q/A pairs, shown per product on the
FAQ page, with admin-editable answers.

Each entry: {id, source, products, category, question, answer, edited}.
An admin curates the answer; only entries with a non-empty answer are
eligible to serve. Persisted to faq_store.json.

v3.1.0 — SEMANTIC MATCHING. match_answer() previously used lexical
Jaccard over raw word tokens at a 0.6 threshold, which only fired on
near-verbatim repeats. Measured against the FAQ question
"Does MyCheckr require an internet connection?":

    Is an internet connection required for MyCheckr?   0.444  MISS
    does the device need internet                      0.222  MISS
    Do I need wifi for MyCheckr?                       0.091  MISS
    can mycheckr work offline                          0.111  MISS

Four compounding causes: Jaccard's union denominator punishes every
extra word; stopwords inflate that union; 0.6 is a high bar for Jaccard;
and there is no semantic layer, so offline/wifi/internet are unrelated
tokens.

Now: cosine similarity over the SAME all-MiniLM-L6-v2 embedding model
already loaded for retrieval (no new dependency, no extra model in
memory), with FAQ question vectors cached and invalidated on store
mtime. Falls back to an improved lexical score — stopword-filtered
overlap coefficient rather than raw Jaccard — when sentence-transformers
isn't importable, so the module still works (and stays unit-testable)
without ML dependencies installed.
"""
import json
import os
import re
import threading
import uuid
import logging

logger = logging.getLogger(__name__)

_PATH = os.getenv("FAQ_STORE_PATH", "faq_store.json")
_lock = threading.Lock()

# Cosine threshold for a semantic hit. 0.75 is deliberately conservative:
# a wrong curated answer is worse than falling through to the full RAG
# pipeline, which will at least ground its answer in the documents.
SEMANTIC_THRESHOLD = float(os.getenv("FAQ_SEMANTIC_THRESHOLD", "0.75"))
# Lexical fallback threshold (overlap coefficient over content words).
LEXICAL_THRESHOLD = float(os.getenv("FAQ_LEXICAL_THRESHOLD", "0.7"))

# Stopwords excluded from lexical scoring. Question words are included
# deliberately: "does/is/can/do" carry almost no topical signal but
# appear in nearly every FAQ question, so counting them both inflates
# scores between unrelated questions and dilutes real overlap.
_STOP = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "doing", "can", "could", "will", "would", "shall",
    "should", "may", "might", "must", "have", "has", "had", "of", "to",
    "in", "on", "at", "for", "with", "from", "by", "about", "as", "into",
    "any", "all", "it", "its", "this", "that", "these", "those", "there",
    "i", "you", "we", "they", "my", "our", "your", "me", "us",
    "and", "or", "but", "if", "then", "so", "than", "how", "what", "when",
    "where", "which", "who", "why", "whose", "whom",
}


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
    """Replaces any existing entries for this source but PRESERVES admin
    edits by question text. Stores both product and category so FAQ
    lookups by either key work (a category-level chat asks for the
    category's questions)."""
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
    _invalidate_cache()


def list_for_product(scope_key: str | None) -> list[dict]:
    """Match by PRODUCT or CATEGORY key — a category-level chat passes the
    category key, a product chat passes the product key."""
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
                _invalidate_cache()
                return it
    return None


def delete_entry(faq_id: str) -> bool:
    with _lock:
        items = _load()
        n = len(items)
        items = [it for it in items if it["id"] != faq_id]
        _save(items)
        if len(items) < n:
            _invalidate_cache()
            return True
        return False


# ── lexical scoring (fallback, and unit-testable without ML deps) ──────

def _tokens(text: str) -> set:
    """Content words only: lowercase, stopwords removed, trivial plural
    stripping so "credentials"/"credential" and "images"/"image" match."""
    raw = re.findall(r"[a-z0-9]+", (text or "").lower())
    out = set()
    for w in raw:
        if w in _STOP or len(w) < 2:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return out


def lexical_score(a: str, b: str) -> float:
    """Overlap coefficient (intersection / smaller set) over content
    words. Unlike Jaccard this doesn't penalise a query for carrying
    extra words the FAQ question lacks — the common case for a real user
    question ("do I need wifi for the mycheckr device in store?").

    Guarded against the failure mode plain overlap has: dividing by the
    SMALLER set means a very short FAQ question scores 1.0 against
    anything containing its one content word — "does mycheckr need
    internet" matched "What is MyCheckr?" perfectly in testing. So a
    single shared word is never enough on its own; where either side has
    fewer than two content words, fall back to Jaccard, which requires
    the questions to be genuinely similar in full.
    """
    A, B = _tokens(a), _tokens(b)
    if not A or not B:
        return 0.0
    inter = len(A & B)
    if inter == 0:
        return 0.0
    smaller = min(len(A), len(B))
    if smaller < 2 or inter < 2:
        # Not enough signal for overlap; require whole-question similarity.
        return inter / len(A | B)
    return inter / smaller


# ── semantic scoring ──────────────────────────────────────────────────

_cache_lock = threading.Lock()
_cache_mtime: float | None = None
_cache_ids: list[str] = []
_cache_vecs = None          # np.ndarray (n, dim), L2-normalised
_semantic_available: bool | None = None


def _invalidate_cache() -> None:
    global _cache_mtime, _cache_ids, _cache_vecs
    with _cache_lock:
        _cache_mtime, _cache_ids, _cache_vecs = None, [], None


def _store_mtime() -> float:
    try:
        return os.path.getmtime(_PATH)
    except OSError:
        return 0.0


def _build_cache(items: list[dict]):
    """Embed every answerable FAQ question once. Rebuilt when the store
    file changes on disk (so an admin edit takes effect without a
    restart) — not per query, which would embed the whole FAQ set on
    every request."""
    global _cache_mtime, _cache_ids, _cache_vecs, _semantic_available
    from embeddings import embed_texts

    answerable = [it for it in items if (it.get("answer") or "").strip()]
    if not answerable:
        _cache_ids, _cache_vecs, _cache_mtime = [], None, _store_mtime()
        return
    _cache_ids = [it["id"] for it in answerable]
    _cache_vecs = embed_texts([it["question"] for it in answerable])
    _cache_mtime = _store_mtime()
    _semantic_available = True
    logger.info(f"FAQ semantic cache built: {len(_cache_ids)} questions")


def _semantic_scores(question: str, items: list[dict]) -> dict[str, float]:
    """{faq_id: cosine} for answerable entries, or {} if embeddings are
    unavailable. Vectors are normalised by embeddings.py, so a dot
    product IS the cosine."""
    global _semantic_available
    if _semantic_available is False:
        return {}
    try:
        from embeddings import embed_query
        with _cache_lock:
            if _cache_vecs is None or _cache_mtime != _store_mtime():
                _build_cache(items)
            ids, vecs = list(_cache_ids), _cache_vecs
        if vecs is None or not ids:
            return {}
        qv = embed_query(question)
        sims = vecs @ qv
        return {fid: float(s) for fid, s in zip(ids, sims)}
    except Exception as e:
        # Missing sentence-transformers, or a model load failure. Log
        # once, then fall back to lexical for the rest of the process
        # rather than retrying (and re-paying the import cost) per query.
        if _semantic_available is not False:
            logger.warning(f"FAQ semantic matching unavailable, using lexical "
                           f"fallback: {e}")
            _semantic_available = False
        return {}


def match_answer(question: str, scope_key: str | None = None,
                 min_score: float | None = None) -> dict | None:
    """Find a curated FAQ answer for `question`, or None to fall through
    to the full RAG pipeline.

    Scores every answerable FAQ question in scope both semantically
    (cosine over MiniLM embeddings) and lexically (stopword-filtered
    overlap), and takes the better of the two normalised against its own
    threshold. Belt-and-braces on purpose: embeddings catch paraphrase
    ("can it work offline" -> "does it require internet"), while the
    lexical path catches exact product names and part numbers that a
    small embedding model can smear together.
    """
    if not (question or "").strip():
        return None

    candidates = [it for it in list_for_product(scope_key)
                  if (it.get("answer") or "").strip()]
    if not candidates:
        return None

    sem = _semantic_scores(question, candidates)

    best, best_norm, best_detail = None, 0.0, None
    for it in candidates:
        s_sem = sem.get(it["id"], 0.0)
        s_lex = lexical_score(question, it["question"])
        # Normalise each against its own threshold so they're comparable,
        # then take the stronger signal. >= 1.0 means "over threshold".
        n_sem = s_sem / SEMANTIC_THRESHOLD if SEMANTIC_THRESHOLD else 0.0
        n_lex = s_lex / LEXICAL_THRESHOLD if LEXICAL_THRESHOLD else 0.0
        norm = max(n_sem, n_lex)
        if norm > best_norm:
            best, best_norm = it, norm
            best_detail = {"semantic": round(s_sem, 3), "lexical": round(s_lex, 3),
                           "matched_by": "semantic" if n_sem >= n_lex else "lexical"}

    override = min_score if min_score is not None else 1.0
    if best and best_norm >= override:
        logger.info(f"FAQ hit ({best_detail['matched_by']}): "
                    f"'{question}' -> '{best['question']}' {best_detail}")
        return {
            "answer": best["answer"],
            "question": best["question"],
            "score": round(best_norm, 3),
            **best_detail,
        }
    return None
