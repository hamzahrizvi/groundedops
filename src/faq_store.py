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

# Candidate shortlisting.
#
# v3.4.1: raised from 0.45/0.34. Those were set loose on the reasoning that
# a wrong suggestion only costs a glance — but in practice near-EVERY
# question produced three options, most unrelated, which trains people to
# ignore the prompt entirely and makes the good suggestions worthless.
# Measured cause: any two questions about the same product clear 0.45
# cosine, and short FAQ entries wreck the lexical side — "What is
# MyCheckr?" has ONE content word, so it scored 0.500 against "what are the
# dimensions for the MyCheckr?" and was offered as a match.
#
# 0.70 on both is roughly the paraphrase/merely-related boundary for
# MiniLM on short questions.
CANDIDATE_COSINE_FLOOR = float(os.getenv("FAQ_CANDIDATE_FLOOR", "0.70"))
CANDIDATE_LEXICAL_FLOOR = float(os.getenv("FAQ_CANDIDATE_LEX_FLOOR", "0.70"))
MAX_CANDIDATES = int(os.getenv("FAQ_MAX_CANDIDATES", "3"))

# Drop candidates far weaker than the best one. Without this, a single
# strong match at 0.88 still gets padded out to three with two 0.71s, which
# reads as "the system is guessing". Suggest ONE option when only one is
# genuinely close.
CANDIDATE_RELATIVE_MARGIN = float(os.getenv("FAQ_CANDIDATE_MARGIN", "0.12"))

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


def _norm_key(k: str | None) -> str:
    """Normalise a product/category key for comparison.

    v3.4.0: matching used to be exact string equality, so a catalog key of
    "MyCheckr" and a stored FAQ key of "mycheckr" (or one with stray
    whitespace) silently matched NOTHING — the FAQ appeared empty for that
    product with no error anywhere. Keys come from different places (the
    catalog UI, the upload headers, the FAQ generate payload), so tolerate
    case and spacing rather than trusting them to agree.
    """
    return (k or "").strip().lower().replace(" ", "-").replace("_", "-")


def _norm_q(q: str | None) -> str:
    """Normalise a question for DUPLICATE detection: casefold, collapse
    whitespace, drop trailing punctuation. "Does it need WiFi?" and
    "does it need wifi" are the same question for storage purposes."""
    t = re.sub(r"\s+", " ", (q or "").strip().lower())
    return t.rstrip("?.! ")


def merge_questions(source: str, products: str, qa_pairs: list[dict],
                    category: str = "") -> dict:
    """NON-DESTRUCTIVE generate (v3.4.0).

    record_questions() replaces every entry for a source, which meant
    generating again wiped manually-added questions and reset curated
    answers. This adds only questions that aren't already present
    ANYWHERE in the same product scope, and never touches an existing
    entry — so "Generate" becomes safe to press repeatedly.

    Duplicate detection is on the normalised question text, so a
    regenerated "Does it need WiFi?" won't be stored again alongside an
    existing "does it need wifi".

    Returns {"added": n, "skipped_duplicates": n, "total": n}.
    """
    with _lock:
        items = _load()
        # Compare against everything in scope, not just this source: the
        # same question generated from two different manuals is still a
        # duplicate to the person reading the FAQ list.
        want = _norm_key(products)
        existing = set()
        for it in items:
            prods = [_norm_key(p) for p in (it.get("products") or "").split(",")]
            if want in prods or want == _norm_key(it.get("category")):
                existing.add(_norm_q(it.get("question")))
                # Also match the wording an edited entry was originally
                # GENERATED with (see update_entry) — re-drafting reproduces
                # something close to the model's own original phrasing, not
                # an admin's rewrite, so without this an edited question
                # comes back as a "new" near-duplicate every time.
                if it.get("original_question"):
                    existing.add(_norm_q(it["original_question"]))

        added = 0
        skipped = 0
        for qa in qa_pairs:
            q = (qa.get("question") or "").strip()
            if not q:
                continue
            if _norm_q(q) in existing:
                skipped += 1
                continue
            existing.add(_norm_q(q))
            items.append({
                "id": str(uuid.uuid4()),
                "source": source,
                "products": products,
                "category": category,
                "question": q,
                "answer": qa.get("answer", ""),
                "edited": False,
                "origin": "generated",
            })
            added += 1
        _save(items)
    _invalidate_cache()
    logger.info(f"FAQ merge for '{source}': +{added}, {skipped} duplicates skipped")
    return {"added": added, "skipped_duplicates": skipped, "total": added + skipped}


def add_entry(question: str, answer: str, products: str = "",
              category: str = "", source: str = "manual") -> dict:
    """Add ONE question by hand. Rejects a duplicate in the same scope so
    the admin gets told rather than quietly creating a second copy."""
    q = (question or "").strip()
    if not q:
        raise ValueError("Question cannot be empty")
    with _lock:
        items = _load()
        want = _norm_key(products)
        for it in items:
            prods = [_norm_key(p) for p in (it.get("products") or "").split(",")]
            if (want in prods or want == _norm_key(it.get("category"))) \
                    and _norm_q(it.get("question")) == _norm_q(q):
                raise ValueError(f"That question already exists: {it['question']!r}")
        entry = {
            "id": str(uuid.uuid4()),
            "source": source,
            "products": products,
            "category": category,
            "question": q,
            "answer": answer or "",
            # Marked edited so a later merge/regeneration can never
            # overwrite a hand-written entry.
            "edited": True,
            "origin": "manual",
        }
        items.append(entry)
        _save(items)
    _invalidate_cache()
    return entry


def update_entry(faq_id: str, question: str | None = None,
                 answer: str | None = None) -> dict | None:
    """Edit the question text and/or the answer. update_answer() could
    only change the answer, so a badly-worded generated question could
    only be deleted and retyped."""
    with _lock:
        items = _load()
        for it in items:
            if it["id"] == faq_id:
                if question is not None and question.strip():
                    new_q = question.strip()
                    # Remember the wording this entry was GENERATED with,
                    # once, before the first edit overwrites it — see the
                    # dedup note in merge_questions.
                    if new_q != it["question"] and "original_question" not in it:
                        it["original_question"] = it["question"]
                    it["question"] = new_q
                if answer is not None:
                    it["answer"] = answer
                it["edited"] = True
                _save(items)
                _invalidate_cache()
                return it
    return None


def delete_all(scope_key: str | None = None, source: str | None = None) -> int:
    """Bulk delete. With no arguments this wipes the ENTIRE FAQ store, so
    the caller is responsible for confirming with the user first — the
    API endpoint requires an explicit confirm flag."""
    with _lock:
        items = _load()
        before = len(items)
        if source:
            keep = [it for it in items if it.get("source") != source]
        elif scope_key and _norm_key(scope_key) != "all":
            want = _norm_key(scope_key)
            keep = []
            for it in items:
                prods = [_norm_key(p) for p in (it.get("products") or "").split(",")]
                if want in prods or want == _norm_key(it.get("category")):
                    continue
                keep.append(it)
        else:
            keep = []
        _save(keep)
    _invalidate_cache()
    removed = before - len(keep)
    logger.warning(f"FAQ bulk delete: removed {removed} entries "
                   f"(scope={scope_key!r}, source={source!r})")
    return removed


def list_for_product(scope_key: str | None) -> list[dict]:
    """Match by PRODUCT or CATEGORY key (case/format tolerant)."""
    items = _load()
    if not scope_key or _norm_key(scope_key) == "all":
        return items
    want = _norm_key(scope_key)
    out = []
    for it in items:
        prods = [_norm_key(p) for p in (it.get("products") or "").split(",")]
        if want in prods or want == _norm_key(it.get("category")):
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

def _gap_key(question: str) -> str:
    """Normalized dedup/lookup key for a gap — same question text asked
    with different casing/punctuation groups under one entry, and this key
    doubles as the entry's addressable id (see dismiss_gap). Lowercased,
    punctuation stripped, whitespace collapsed: "Does it need WiFi?" and
    "does it need wifi" must land on the same key, or times_asked stops
    meaning what the admin page implies it means."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (question or "").lower())).strip()


def record_gap(question: str, scope_key: str | None,
               shown: list[str] | None = None, reason: str = "") -> None:
    """Record a question the FAQ could not answer.

    Two distinct causes land here, kept separable via `reason`:
      - the FAQ suggestion flow found nothing close enough, or the user
        said "I'm asking something else" (reason left blank, matching the
        original call site — this predates the `reason` field)
      - the main /query path itself came up with a genuine miss: low
        retrieval confidence, or an answer that failed grounding and was
        suppressed (reason e.g. "low_retrieval_confidence",
        "ungrounded_answer_suppressed")

    Either way, this is the most valuable by-product of asking instead of
    guessing: an explicit list of questions your documentation is being
    asked but doesn't (yet) answer. Surface it to the admin and it becomes
    the FAQ backlog, prioritised by real demand.
    """
    q = (question or "").strip()
    if not q:
        return
    key = _gap_key(q)
    if not key:
        return
    now = __import__("time").time()
    try:
        with _lock:
            gaps = []
            if os.path.exists(_GAP_PATH):
                try:
                    with open(_GAP_PATH) as f:
                        gaps = json.load(f)
                except Exception:
                    gaps = []
            for g in gaps:
                if g.get("id") == key:
                    # Visitors ask the same thing a dozen ways in casing and
                    # punctuation, which _gap_key already collapses — merge
                    # into the existing entry instead of piling up one row
                    # per ask, so times_asked means what the admin page
                    # implies it means.
                    g["times_asked"] = int(g.get("times_asked", 1)) + 1
                    g["ts"] = now
                    if scope_key and not g.get("scope"):
                        # Keep the most specific scope seen — a gap first
                        # asked with no product and later inside one belongs
                        # to that product.
                        g["scope"] = scope_key
                    if reason and not g.get("reason"):
                        g["reason"] = reason
                    if shown:
                        g["suggestions_shown"] = shown
                    with open(_GAP_PATH, "w") as f:
                        json.dump(gaps, f, indent=2)
                    return
            gaps.append({
                "id": key,
                "question": q,               # first-seen spelling, as asked
                "scope": scope_key,
                "reason": reason,
                "suggestions_shown": shown or [],
                "times_asked": 1,
                "ts": now,
                "resolved": False,
                "resolved_faq_id": None,
                "spam": False,
            })
            if len(gaps) > 500:
                gaps = gaps[-500:]            # keep it bounded
            with open(_GAP_PATH, "w") as f:
                json.dump(gaps, f, indent=2)
    except Exception as e:
        logger.warning(f"could not record FAQ gap (non-fatal): {e}")


def _normalize_gap(g: dict) -> dict:
    """Backfill fields on gaps recorded before `id`/`times_asked`/`resolved`
    existed (the gap log predates them), so every caller can rely on the
    full shape without special-casing older entries."""
    g.setdefault("id", _gap_key(g.get("question")))
    g.setdefault("times_asked", 1)
    g.setdefault("resolved", False)
    g.setdefault("resolved_faq_id", None)
    g.setdefault("reason", "")
    g.setdefault("spam", False)
    return g


def list_gaps(scope_key: str | None = None, include_resolved: bool = False,
              include_spam: bool = False) -> list[dict]:
    """Most-asked first — the order the console presents as a to-do list.

    spam is excluded by default same as resolved — it's still being
    tracked (record_gap keeps incrementing times_asked on it), just kept
    off the working list, unlike resolved which is a one-time hide.
    """
    if not os.path.exists(_GAP_PATH):
        return []
    try:
        with open(_GAP_PATH) as f:
            gaps = json.load(f)
    except Exception:
        return []
    gaps = [_normalize_gap(g) for g in gaps]
    if not include_resolved:
        gaps = [g for g in gaps if not g.get("resolved")]
    if not include_spam:
        gaps = [g for g in gaps if not g.get("spam")]
    if scope_key:
        gaps = [g for g in gaps if g.get("scope") == scope_key]
    return sorted(gaps, key=lambda g: (-int(g.get("times_asked", 1)), -g.get("ts", 0)))


def resolve_gap_matching(question: str, faq_id: str | None = None) -> int:
    """Mark any open gap matching `question` as resolved.

    Called when an admin saves a curated answer, so answering a question
    from the gaps list clears it without a second explicit action. A gap is
    marked resolved rather than deleted — deleting would lose the "asked
    Nx" evidence that justified writing the answer, and would let the same
    question silently re-accumulate as if it were new. Returns how many
    entries were resolved (0 or 1 in practice, since gaps are deduped by
    normalized question on write).
    """
    key = _gap_key(question)
    if not key or not os.path.exists(_GAP_PATH):
        return 0
    try:
        with _lock:
            with open(_GAP_PATH) as f:
                gaps = json.load(f)
            n = 0
            for g in gaps:
                if (g.get("id") or _gap_key(g.get("question"))) == key and not g.get("resolved"):
                    g["resolved"] = True
                    g["resolved_faq_id"] = faq_id
                    g["resolved_at"] = __import__("time").time()
                    n += 1
            if n:
                with open(_GAP_PATH, "w") as f:
                    json.dump(gaps, f, indent=2)
            return n
    except Exception as e:
        logger.warning(f"could not resolve FAQ gap (non-fatal): {e}")
        return 0


def gap_stats() -> dict:
    if not os.path.exists(_GAP_PATH):
        return {"open": 0, "resolved": 0, "total_asks": 0}
    try:
        with open(_GAP_PATH) as f:
            gaps = json.load(f)
    except Exception:
        return {"open": 0, "resolved": 0, "total_asks": 0}
    open_gaps = [g for g in gaps if not g.get("resolved")]
    return {
        "open": len(open_gaps),
        "resolved": len(gaps) - len(open_gaps),
        "total_asks": sum(int(g.get("times_asked", 1)) for g in open_gaps),
    }


def dismiss_gap(gap_id: str) -> bool:
    """Hard-delete every recorded entry for one normalized question — a
    one-off clear. Unlike mark_spam, a dismissed question that gets asked
    again starts a fresh entry, since record_gap only checks IDs still in
    the file. Returns whether anything was actually removed."""
    if not os.path.exists(_GAP_PATH):
        return False
    try:
        with _lock:
            with open(_GAP_PATH) as f:
                gaps = json.load(f)
            n = len(gaps)
            gaps = [g for g in gaps
                    if (g.get("id") or _gap_key(g.get("question"))) != gap_id]
            with open(_GAP_PATH, "w") as f:
                json.dump(gaps, f, indent=2)
            return len(gaps) < n
    except Exception as e:
        logger.warning(f"could not dismiss FAQ gap (non-fatal): {e}")
        return False


def dismiss_gap_bulk(gap_ids: list[str]) -> int:
    """dismiss_gap for many at once. Returns how many were removed."""
    if not gap_ids or not os.path.exists(_GAP_PATH):
        return 0
    ids = set(gap_ids)
    try:
        with _lock:
            with open(_GAP_PATH) as f:
                gaps = json.load(f)
            n = len(gaps)
            gaps = [g for g in gaps
                    if (g.get("id") or _gap_key(g.get("question"))) not in ids]
            with open(_GAP_PATH, "w") as f:
                json.dump(gaps, f, indent=2)
            return n - len(gaps)
    except Exception as e:
        logger.warning(f"could not bulk-dismiss FAQ gaps (non-fatal): {e}")
        return 0


def mark_spam(gap_id: str) -> bool:
    """Flag a gap as spam rather than deleting it — unlike dismiss_gap,
    record_gap's merge-by-id keeps finding this entry on a repeat ask, so
    it never resurfaces on the working list even if asked again. Returns
    whether a matching entry was found."""
    if not os.path.exists(_GAP_PATH):
        return False
    try:
        with _lock:
            with open(_GAP_PATH) as f:
                gaps = json.load(f)
            found = False
            for g in gaps:
                if (g.get("id") or _gap_key(g.get("question"))) == gap_id:
                    g["spam"] = True
                    found = True
            if found:
                with open(_GAP_PATH, "w") as f:
                    json.dump(gaps, f, indent=2)
            return found
    except Exception as e:
        logger.warning(f"could not mark FAQ gap as spam (non-fatal): {e}")
        return False


def mark_spam_bulk(gap_ids: list[str]) -> int:
    """mark_spam for many at once. Returns how many were flagged."""
    if not gap_ids or not os.path.exists(_GAP_PATH):
        return 0
    ids = set(gap_ids)
    try:
        with _lock:
            with open(_GAP_PATH) as f:
                gaps = json.load(f)
            n = 0
            for g in gaps:
                if (g.get("id") or _gap_key(g.get("question"))) in ids:
                    g["spam"] = True
                    n += 1
            if n:
                with open(_GAP_PATH, "w") as f:
                    json.dump(gaps, f, indent=2)
            return n
    except Exception as e:
        logger.warning(f"could not bulk-mark FAQ gaps as spam (non-fatal): {e}")
        return 0


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
    # Relative cut: keep only what's competitive with the best match.
    _best = scored[0][0]
    scored = [t for t in scored if t[0] >= _best - CANDIDATE_RELATIVE_MARGIN]
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
