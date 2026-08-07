"""FAQ store — admin-curated Q/A pairs, surfaced per product.

Each entry: {id, source, products, category, question, answer, edited}.
An admin curates the answer; only entries with a non-empty answer are
eligible to serve. Persisted to faq_store.json.

v3.2.0 — ASK, DON'T GUESS.
==========================
Earlier versions tried to decide automatically whether a user's question
was equivalent to a curated one, and served the curated answer if so.
Two implementations failed in production:

  v3.0  lexical Jaccard @0.6 — only fired on near-verbatim repeats, so
        the FAQ was effectively dead.
  v3.1  bi-encoder cosine, then a cross-encoder "verifier" — served
        "No, it provides instant results with no requirement for
        internet." in response to "Does the MyCheckr have WiFi or
        Ethernet ports?". Semantically adjacent, factually opposite.
        The verifier was ms-marco, a passage-RELEVANCE ranker: for that
        pair high relevance is the CORRECT output, because the texts
        genuinely are about the same topic. Relevance is not equivalence.

The lesson is that deciding equivalence is the hard part, and it's the
part a human does effortlessly. So we stop deciding.

Now: rank candidates loosely (recall-oriented), then let the USER pick.

    suggest_candidates()  -> up to 3 plausible curated questions
    get_by_id()           -> serve the answer the user actually chose
    record_gap()          -> log "none of these", i.e. a real FAQ gap

Only ONE case still auto-serves without asking: a near-verbatim lexical
match (>= AUTO_SERVE_LEXICAL). That covers the two cases where there is
no ambiguity to resolve — the user tapped a suggested question chip (so
the query IS the curated question), or typed it essentially verbatim.
Everything else asks. This keeps the common path instant while making the
dangerous path impossible.

Ranking uses the same all-MiniLM-L6-v2 model already loaded for
retrieval, so no extra model and no extra memory. Where it isn't
importable, lexical ranking alone still produces a usable shortlist —
which is acceptable now precisely BECAUSE the user confirms.
"""
import json
import os
import re
import threading
import uuid
import logging

logger = logging.getLogger(__name__)

_PATH = os.getenv("FAQ_STORE_PATH", "faq_store.json")
_GAP_PATH = os.getenv("FAQ_GAP_PATH", "faq_gaps.json")
_lock = threading.Lock()

# Master kill switch — FAQ_ENABLED=off routes every query through
# retrieval + grounding.
FAQ_ENABLED = os.getenv("FAQ_ENABLED", "on").strip().lower() not in ("off", "0", "false")

# Auto-serve without asking. Deliberately near-1.0: this is for verbatim
# repeats and tapped suggestion chips only.
AUTO_SERVE_LEXICAL = float(os.getenv("FAQ_AUTO_SERVE", "0.95"))

# Candidate shortlisting. Loose on purpose — a wrong SUGGESTION costs the
# user one glance, whereas a wrong ANSWER costs them the truth. Cosine
# floor is low; the cap on count is what keeps the UI sane.
CANDIDATE_COSINE_FLOOR = float(os.getenv("FAQ_CANDIDATE_FLOOR", "0.45"))
CANDIDATE_LEXICAL_FLOOR = float(os.getenv("FAQ_CANDIDATE_LEX_FLOOR", "0.34"))
MAX_CANDIDATES = int(os.getenv("FAQ_MAX_CANDIDATES", "3"))

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


# ── persistence ───────────────────────────────────────────────────────

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
    """Replaces entries for this source but PRESERVES admin edits by
    question text."""
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
    """Match by PRODUCT or CATEGORY key."""
    items = _load()
    if not scope_key or scope_key == "all":
        return items
    out = []
    for it in items:
        prods = (it.get("products") or "").split(",")
        if scope_key in prods or scope_key == it.get("category"):
            out.append(it)
    return out


def get_by_id(faq_id: str) -> dict | None:
    """Fetch one entry by id. Used to serve the answer the user EXPLICITLY
    selected — no matching involved, so no possibility of a mismatch."""
    for it in _load():
        if it["id"] == faq_id and (it.get("answer") or "").strip():
            return it
    return None


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


# ── FAQ gap log ───────────────────────────────────────────────────────

def record_gap(question: str, scope_key: str | None,
               shown: list[str] | None = None) -> None:
    """Record a question the FAQ could not answer — either nothing was
    close enough to suggest, or the user said "I'm asking something
    else".

    This is the most valuable by-product of asking instead of guessing:
    an explicit, user-confirmed list of questions your documentation is
    being asked but your curated FAQ doesn't cover. Surface it to the
    admin and it becomes the FAQ backlog, prioritised by real demand.
    """
    try:
        with _lock:
            gaps = []
            if os.path.exists(_GAP_PATH):
                try:
                    with open(_GAP_PATH) as f:
                        gaps = json.load(f)
                except Exception:
                    gaps = []
            gaps.append({
                "question": question,
                "scope": scope_key,
                "suggestions_shown": shown or [],
                "ts": __import__("time").time(),
            })
            with open(_GAP_PATH, "w") as f:
                json.dump(gaps[-500:], f, indent=2)   # keep it bounded
    except Exception as e:
        logger.warning(f"could not record FAQ gap (non-fatal): {e}")


def list_gaps(scope_key: str | None = None) -> list[dict]:
    if not os.path.exists(_GAP_PATH):
        return []
    try:
        with open(_GAP_PATH) as f:
            gaps = json.load(f)
    except Exception:
        return []
    if scope_key:
        gaps = [g for g in gaps if g.get("scope") == scope_key]
    # Most-asked first — that's the curation priority.
    counts: dict[str, int] = {}
    for g in gaps:
        k = (g.get("question") or "").strip().lower()
        counts[k] = counts.get(k, 0) + 1
    for g in gaps:
        g["times_asked"] = counts.get((g.get("question") or "").strip().lower(), 1)
    return sorted(gaps, key=lambda g: (-g["times_asked"], -g.get("ts", 0)))


# ── lexical scoring ───────────────────────────────────────────────────

def _tokens(text: str) -> set:
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
    """Overlap coefficient over content words, guarded against the
    short-question false positive (a 1-word FAQ question would otherwise
    score 1.0 against anything containing that word — measured: "does
    mycheckr need internet" scored 1.0 against "What is MyCheckr?")."""
    A, B = _tokens(a), _tokens(b)
    if not A or not B:
        return 0.0
    inter = len(A & B)
    if inter == 0:
        return 0.0
    smaller = min(len(A), len(B))
    if smaller < 2 or inter < 2:
        return inter / len(A | B)
    return inter / smaller


# ── semantic ranking ──────────────────────────────────────────────────

_cache_lock = threading.Lock()
_cache_mtime: float | None = None
_cache_ids: list[str] = []
_cache_vecs = None
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
    logger.info(f"FAQ ranking cache built: {len(_cache_ids)} questions")


def _semantic_scores(question: str, items: list[dict]) -> dict[str, float]:
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
        return {fid: float(s) for fid, s in zip(ids, vecs @ qv)}
    except Exception as e:
        if _semantic_available is not False:
            logger.warning(f"FAQ semantic ranking unavailable, lexical only: {e}")
            _semantic_available = False
        return {}


# ── the entry point ───────────────────────────────────────────────────

def suggest_candidates(question: str, scope_key: str | None = None) -> dict:
    """Decide what to do with an incoming question.

    Returns one of:

      {"mode": "answer",       entry, score}
          Near-verbatim match — serve it, nothing to disambiguate.

      {"mode": "disambiguate", candidates: [{id, question, score}, ...]}
          Plausible curated questions. The CALLER must present these and
          let the user choose; it must NOT pick one itself. That choice
          is the whole point.

      {"mode": "none"}
          Nothing close. Go straight to retrieval + generation.

    Note there is no confidence threshold separating "confident enough to
    answer" from "not confident" beyond the near-verbatim case. That
    judgement is exactly what kept going wrong, so it's been handed to
    the person who can actually make it.
    """
    if not FAQ_ENABLED or not (question or "").strip():
        return {"mode": "none"}

    pool = [it for it in list_for_product(scope_key)
            if (it.get("answer") or "").strip()]
    if not pool:
        return {"mode": "none"}

    sem = _semantic_scores(question, pool)

    scored = []
    for it in pool:
        s_lex = lexical_score(question, it["question"])
        s_sem = sem.get(it["id"], 0.0)
        if s_lex >= AUTO_SERVE_LEXICAL:
            logger.info(f"FAQ auto-serve (near-verbatim {s_lex:.3f}): {question!r}")
            return {"mode": "answer", "entry": it, "score": round(s_lex, 3)}
        if s_sem >= CANDIDATE_COSINE_FLOOR or s_lex >= CANDIDATE_LEXICAL_FLOOR:
            scored.append((max(s_sem, s_lex), it, s_sem, s_lex))

    if not scored:
        record_gap(question, scope_key, [])
        logger.info(f"FAQ: no candidates for {question!r} — going to retrieval")
        return {"mode": "none"}

    scored.sort(key=lambda t: -t[0])
    cands = [{
        "id": it["id"],
        "question": it["question"],
        "score": round(s, 3),
        "semantic": round(sem_s, 3),
        "lexical": round(lex_s, 3),
    } for s, it, sem_s, lex_s in scored[:MAX_CANDIDATES]]

    logger.info(f"FAQ disambiguate {question!r} -> "
                + "; ".join(f"{c['question'][:40]} ({c['score']})" for c in cands))
    return {"mode": "disambiguate", "candidates": cands}


# Back-compat shim: older callers expect match_answer(). It now only ever
# returns a NEAR-VERBATIM hit, never a guess — anything ambiguous returns
# None so the caller falls through rather than silently asserting.
def match_answer(question: str, scope_key: str | None = None,
                 min_score: float | None = None) -> dict | None:
    r = suggest_candidates(question, scope_key)
    if r["mode"] == "answer":
        e = r["entry"]
        return {"answer": e["answer"], "question": e["question"],
                "score": r["score"], "mode": "answer"}
    return None
