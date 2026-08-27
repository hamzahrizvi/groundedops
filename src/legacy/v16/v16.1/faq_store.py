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


def _read_gaps() -> list[dict]:
    """The gap log, or an empty list. Six places opened this file by hand
    with slightly different error handling; this is that, once."""
    if not os.path.exists(_GAP_PATH):
        return []
    try:
        with open(_GAP_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"gap log read failed: {e}")
        return []


def _write_gaps(gaps: list[dict]) -> None:
    with open(_GAP_PATH, "w", encoding="utf-8") as f:
        json.dump(gaps, f, indent=2)


# ── what is worth putting on the backlog ──────────────────────────────
# The gap log is a work queue, so an entry has to be something someone
# could actually write an answer to. It was recording everything a visitor
# typed, which put "talk to sales", "no" and bare product names like
# "nv9usb" on the list beside real questions -- noise that cannot be
# curated away because it is not a question.
#
# Filtered here rather than at the call sites: record_gap has six callers
# and a rule enforced in one place cannot be forgotten in a seventh.

# Things a visitor says that are not questions. Intent phrases are matched
# as a whole string, not a substring: "talk to sales" is noise, but "who do
# I talk to about sales pricing" is a real question.
_GAP_STOPWORDS = {
    "yes", "no", "ok", "okay", "yep", "nope", "sure", "thanks", "thank you",
    "ta", "cheers", "hi", "hello", "hey", "bye", "goodbye", "help", "test",
    "talk to sales", "talk to support", "speak to sales", "speak to support",
    "contact sales", "contact support", "sales", "support",
    "ask a question", "ask about a product", "general question",
}

# A question usually announces itself: it asks something, or it starts with
# an interrogative. Anything shorter than this that does neither is almost
# always a fragment or a product name typed on its own.
_INTERROGATIVE = {
    "what", "whats", "how", "why", "when", "where", "which", "who", "whose",
    "can", "could", "does", "do", "did", "is", "are", "was", "will", "would",
    "should", "may", "might", "any", "list", "give", "show", "tell", "explain",
}
_MIN_WORDS_WITHOUT_MARKER = 4


def is_curatable_question(question: str) -> bool:
    """Whether this is worth adding to the FAQ backlog.

    Deliberately permissive: a false negative loses one real question from
    the queue, which is recoverable because the visitor will ask again; a
    false positive puts permanent noise on a list someone has to work
    through by hand.
    """
    q = (question or "").strip()
    if not q:
        return False
    key = _gap_key(q)
    if not key or key in _GAP_STOPWORDS:
        return False
    words = key.split()
    if "?" in q or (words and words[0] in _INTERROGATIVE):
        return True
    return len(words) >= _MIN_WORDS_WITHOUT_MARKER


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
    if not is_curatable_question(q):
        logger.debug(f"gap not recorded (not a curatable question): {q[:60]!r}")
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


# ── grouping near-duplicate gaps ──────────────────────────────────────
# _gap_key already folds together questions that differ only by case or
# punctuation, so "Does it need WiFi?" and "does it need wifi" are one
# entry. It cannot fold "does it need wifi" and "is an internet connection
# required" — different words, same question — so the console showed those
# as two unrelated items each asked once, understating real demand and
# splitting the backlog.
#
# This groups at READ time and never rewrites the stored gaps. That matters:
# clustering is a judgement call with a threshold, and a wrong merge that
# has been written to disk cannot be undone. Grouped for display, the
# underlying questions stay separate and a threshold change re-groups them.

# Cosine similarity above which two questions are treated as the same ask.
#
# 0.90, chosen by sweeping the real gap log (82 questions) rather than by
# intuition -- and intuition was wrong. 0.82 looked conservative but merged
# five DIFFERENT MyCheckr questions ("what hardware does it include",
# "how can it be mounted", "does it need an internet connection") into one
# cluster at 0.84-0.90, because on a short question the product name
# dominates the embedding and any two questions about the same product look
# alike. At 0.92 the opposite starts: "list all protocols supported by nv9"
# splits away from the protocol cluster it belongs to.
#
# At 0.90 every merge in the real log is correct -- six phrasings of "what
# is the nv9 pinout", four of "what protocols does it support", two of
# "what voltage", three of "does it have an ethernet port".
#
# Re-sweep this if the corpus changes character; questions from a different
# product family may sit at different similarities.
GAP_CLUSTER_THRESHOLD = float(os.getenv("GAP_CLUSTER_THRESHOLD", "0.90"))

_gap_cache_lock = threading.Lock()
_gap_cache_mtime = None
_gap_cache_vecs = None
_gap_cache_texts: list[str] = []


def _gap_store_mtime() -> float:
    try:
        return os.path.getmtime(_GAP_PATH)
    except OSError:
        return 0.0


def _gap_vectors(questions: list[str]):
    """Embeddings for the gap questions, cached on the gap file's mtime -
    same pattern as _build_cache above. Returns None if embeddings are
    unavailable, and the caller falls back to lexical grouping."""
    global _gap_cache_mtime, _gap_cache_vecs, _gap_cache_texts
    if _semantic_available is False:
        return None
    try:
        from embeddings import embed_texts
        with _gap_cache_lock:
            mt = _gap_store_mtime()
            if (_gap_cache_vecs is None or _gap_cache_mtime != mt
                    or _gap_cache_texts != questions):
                _gap_cache_vecs = embed_texts(questions)
                _gap_cache_texts = list(questions)
                _gap_cache_mtime = mt
            return _gap_cache_vecs
    except Exception as e:
        logger.warning(f"gap clustering: embeddings unavailable, "
                       f"falling back to lexical ({e})")
        return None


def _lexical_similarity(a: str, b: str) -> float:
    """Jaccard over content words. The fallback when embeddings are not
    importable -- weaker than the semantic path (it cannot see that
    "internet" and "wifi" are related) but it still catches rewordings that
    share most of their words, which is better than exact-match only."""
    stop = {"a", "an", "the", "is", "are", "do", "does", "did", "can", "could",
            "will", "would", "it", "its", "to", "of", "for", "on", "in", "and",
            "or", "with", "i", "my", "you", "your", "we", "have", "has", "be"}
    wa = {w for w in _gap_key(a).split() if w not in stop}
    wb = {w for w in _gap_key(b).split() if w not in stop}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def cluster_gaps(gaps: list[dict],
                 threshold: float | None = None) -> list[dict]:
    """Group near-duplicate questions into one entry each.

    Returns a list of CLUSTERS in the same shape as a gap, plus:
        variants     - the other questions folded in, most-asked first
        times_asked  - the sum across the cluster, i.e. real demand
        cluster_size - how many distinct questions it represents

    Clustering is greedy and single-pass: gaps are taken most-asked first,
    and each becomes a new cluster or joins the first existing one it is
    close enough to. Greedy is the right shape here -- the alternative
    (proper agglomerative clustering) buys accuracy that a 0.82 threshold
    on short questions does not justify, and makes the result depend on
    ordering in ways that are harder to explain to whoever reads the list.

    Only gaps sharing a scope are ever compared. Two products can
    legitimately be asked the same question and they are separate backlog
    items -- merging them across products would hide which product is
    underserved, which is what the page exists to show.
    """
    if not gaps:
        return []
    thr = GAP_CLUSTER_THRESHOLD if threshold is None else threshold

    ordered = sorted(gaps, key=lambda g: (-int(g.get("times_asked", 1)),
                                          -g.get("ts", 0)))
    questions = [g.get("question", "") for g in ordered]
    vecs = _gap_vectors(questions)

    clusters: list[dict] = []
    heads: list[int] = []          # index into `ordered` of each cluster head

    for i, g in enumerate(ordered):
        placed = False
        for ci, hi in enumerate(heads):
            if (g.get("scope") or "") != (ordered[hi].get("scope") or ""):
                continue
            if vecs is not None:
                sim = float(vecs[i] @ vecs[hi])
            else:
                sim = _lexical_similarity(questions[i], questions[hi])
            if sim >= thr:
                c = clusters[ci]
                c["variants"].append({
                    "id": g.get("id"),
                    "question": g.get("question"),
                    "times_asked": int(g.get("times_asked", 1)),
                    "ts": g.get("ts", 0),
                    "similarity": round(sim, 3),
                })
                c["times_asked"] += int(g.get("times_asked", 1))
                c["cluster_size"] += 1
                # The cluster's timestamp is the most RECENT ask in it, so
                # "sort by latest" means what a reader expects.
                c["ts"] = max(c.get("ts", 0), g.get("ts", 0))
                c["member_ids"].append(g.get("id"))
                placed = True
                break
        if not placed:
            head = dict(g)
            head["variants"] = []
            head["cluster_size"] = 1
            head["member_ids"] = [g.get("id")]
            head["times_asked"] = int(g.get("times_asked", 1))
            clusters.append(head)
            heads.append(i)

    for c in clusters:
        c["variants"].sort(key=lambda v: -v["times_asked"])
    return sorted(clusters, key=lambda c: (-c["times_asked"], -c.get("ts", 0)))


# ── moving or removing everything filed under a product ───────────────
# Deleting a product used to remove only its catalogue row, leaving its
# documents, answers and questions tagged to a key that no longer existed.
# That is how `nv9st` and `coin_hoppers` became keys with real content
# behind them and no product in front -- content nobody could find through
# the console, because the console builds its lists from the catalogue.

def _entry_products(item: dict) -> list[str]:
    raw = item.get("products") or item.get("product") or ""
    return [k.strip() for k in str(raw).split(",") if k.strip()]


def count_for_product(product_key: str) -> dict:
    """What is filed under this product, so an operator can be told what a
    deletion would affect before confirming it."""
    if not product_key:
        return {"answers": 0, "questions": 0}
    with _lock:
        items = _load()
    answers = sum(1 for it in items if product_key in _entry_products(it))
    gaps = [g for g in _read_gaps() if g.get("scope") == product_key]
    return {"answers": answers, "questions": len(gaps)}


def retag_product(old_key: str, new_key: str | None) -> dict:
    """Move FAQ entries and gaps from one product to another, or untag them
    when new_key is None. Returns what moved."""
    if not old_key or old_key == new_key:
        return {"answers": 0, "questions": 0}

    moved_answers = 0
    with _lock:
        items = _load()
        for it in items:
            keys = _entry_products(it)
            if old_key not in keys:
                continue
            keys = [k for k in keys if k != old_key]
            if new_key and new_key not in keys:
                keys.append(new_key)
            it["products"] = ",".join(keys)
            it.pop("product", None)
            moved_answers += 1
        if moved_answers:
            _save(items)

    moved_gaps = 0
    with _lock:
        gaps = _read_gaps()
        for g in gaps:
            if g.get("scope") == old_key:
                g["scope"] = new_key
                moved_gaps += 1
        if moved_gaps:
            _write_gaps(gaps)

    logger.info(f"retag {old_key!r} -> {new_key!r}: {moved_answers} answer(s), "
                f"{moved_gaps} question(s)")
    return {"answers": moved_answers, "questions": moved_gaps}


def delete_for_product(product_key: str) -> dict:
    """Delete FAQ entries and gaps belonging ONLY to this product.

    An answer shared with another product is retagged rather than deleted,
    for the same reason as db.delete_by_product: removing a shared answer
    because one of its products went away would take it from a product
    nobody touched.
    """
    if not product_key:
        return {"answers": 0, "questions": 0, "kept_shared": 0}

    deleted_answers = kept = 0
    with _lock:
        items = _load()
        out = []
        for it in items:
            keys = _entry_products(it)
            if product_key not in keys:
                out.append(it)
                continue
            remaining = [k for k in keys if k != product_key]
            if remaining:
                it["products"] = ",".join(remaining)
                it.pop("product", None)
                out.append(it)
                kept += 1
            else:
                deleted_answers += 1
        _save(out)

    with _lock:
        gaps = _read_gaps()
        keep = [g for g in gaps if g.get("scope") != product_key]
        deleted_gaps = len(gaps) - len(keep)
        if deleted_gaps:
            _write_gaps(keep)

    logger.info(f"delete_for_product {product_key!r}: {deleted_answers} answer(s), "
                f"{deleted_gaps} question(s); {kept} shared answer(s) kept")
    return {"answers": deleted_answers, "questions": deleted_gaps,
            "kept_shared": kept}


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

    # Absolute floor first. The relative cut below keeps whatever is
    # competitive with the BEST match, which says nothing about whether the
    # best match is any good: for "give nv9 pinout?" the top FAQ was "What is
    # the NV9USB+ Range?" -- same product, unrelated question -- and it got
    # offered because nothing beat it. If the best candidate is not at least
    # this similar, there is no useful suggestion to make and retrieval should
    # handle the question instead.
    # An absolute floor alone is not enough: measured, "give nv9 pinout?"
    # scored 0.720 against "What is the NV9USB+ Range?" -- purely because both
    # mention NV9 -- so any floor low enough to admit real matches admits that
    # too. The discriminating signal is already computed: LEXICAL overlap was
    # 0.000. Semantic-only agreement with no shared content word is exactly the
    # "same topic, different question" case, and suggesting it wastes the
    # visitor's click. Require either a shared word, or near-duplicate phrasing.
    _best_score, _, _best_sem, _best_lex = scored[0]
    _sem_only_min = float(os.getenv("FAQ_SEMANTIC_ONLY_MIN", "0.85"))
    if _best_lex <= 0.0 and _best_sem < _sem_only_min:
        record_gap(question, scope_key, [])
        logger.info(f"FAQ: top match {_best_sem:.3f} semantic with no lexical "
                    f"overlap for {question!r} - going to retrieval")
        return {"mode": "none"}

    _floor = float(os.getenv("FAQ_CANDIDATE_MIN_SCORE", "0.45"))
    if scored[0][0] < _floor:
        record_gap(question, scope_key, [])
        logger.info(f"FAQ: best candidate {scored[0][0]:.3f} < {_floor} for "
                    f"{question!r} - going to retrieval")
        return {"mode": "none"}

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
