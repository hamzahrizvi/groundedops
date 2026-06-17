"""
Pure text-processing utilities — no ML dependencies.

Kept separate from grounding.py/llm.py/structure.py so this logic can be
unit-tested without pulling in sentence-transformers, chromadb, or hitting
Ollama/DeepSeek.
"""

import re

REFUSAL_PHRASE = "i could not find that in the knowledge base"

REFUSAL_PHRASE_VARIANTS = [
    REFUSAL_PHRASE,
    "there is no information about",
    "the context does not contain",
    "does not contain this information",
    "no information about this in the",
]

MIN_UNIT_LEN = 12

# Known multi-word brand/technical terms that must survive the camelCase
# splitter intact (see fix_camel_case below). Order doesn't matter for
# correctness but longer/more-specific terms first avoids partial shadowing
# if you extend this list later.
PROTECTED_TERMS = ["MyConnect", "MyCheckr", "WiFi", "GPIO"]

# Words that, when they're the LAST word of a line, strongly suggest the
# line was cut off mid-thought (table-cell or chunk-boundary truncation)
# rather than being a genuine, complete checklist item.
TRAILING_STOPWORDS = {
    "and", "or", "the", "a", "an", "with", "to", "of", "in", "on",
    "for", "but", "is", "are", "was", "were", "if", "from",
}

LIST_LINE_RE = re.compile(r"^\s*(?:[-*•☐]|\d+[.)])\s+")
STEP_HEADER_RE = re.compile(r"^step\s+\d+", re.IGNORECASE)


# ── camelCase / merge-artifact cleanup ──────────────────────────────────

def fix_camel_case(line: str) -> str:
    """
    Insert a space at lower->upper case boundaries to fix PDF extraction
    artifacts like "deviceCategory" -> "device Category", WITHOUT breaking
    known brand/technical terms that happen to contain such a boundary
    themselves.

    BUG FIXED: the original regex (no term protection) silently mangled
    "MyConnect" -> "My Connect" and "MyCheckr" -> "My Checkr" on every
    extracted/generated answer — visible in production transcripts where
    the assistant kept saying "My Connect App" and "My Checkr" instead of
    the correct product names.
    """
    placeholders: dict[str, str] = {}
    for i, term in enumerate(PROTECTED_TERMS):
        if term in line:
            placeholder = f"\x00{i}\x00"
            line = line.replace(term, placeholder)
            placeholders[placeholder] = term

    line = re.sub(r"([a-z])([A-Z])", r"\1 \2", line)

    for placeholder, term in placeholders.items():
        line = line.replace(placeholder, term)

    return line


def clean_table_artifacts(line: str) -> str:
    """
    Fix checkbox/table-column-merge artifacts and dangling truncated
    parentheses commonly produced by PDF table extraction.

    "activity☐Category Check"   -> "activity\n☐ Category Check"
    "Fail-safe NC wiring (if"   -> "Fail-safe NC wiring"   (dangling paren stripped)
    """
    line = re.sub(r"(\S)☐", r"\1\n☐", line)
    line = re.sub(r"☐(\S)", r"☐ \1", line)

    if line.count("(") > line.count(")"):
        line = re.sub(r"\s*\([^)]*$", "", line)

    return line.strip()


# ── split_units (for grounding NLI checks) ──────────────────────────────

def split_units(answer: str, min_len: int = MIN_UNIT_LEN) -> list[str]:
    """
    Split an answer into checkable units for grounding verification.
    Handles both prose (sentence-boundary split) and numbered/bulleted
    lists (newline split + strip list markers).
    """
    lines = [l.strip() for l in answer.split("\n") if l.strip()]

    units = []
    for line in lines:
        cleaned = re.sub(r"^(\d+[\.\)]|\-|\*)\s*", "", line)
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        for s in sentences:
            s = s.strip().rstrip(".")
            if len(s) >= min_len:
                units.append(s)

    return units


# ── refusal handling ─────────────────────────────────────────────────────

def is_refusal(answer: str, phrases: list[str] | None = None) -> bool:
    """True if the answer text contains any known refusal phrasing."""
    phrases = phrases or REFUSAL_PHRASE_VARIANTS
    lower = answer.lower()
    return any(p in lower for p in phrases)


def truncate_after_refusal(
    text: str,
    refusal_phrases: list[str] | None = None,
) -> str:
    """
    Small local models sometimes emit a correct(-ish) refusal sentence and
    then continue rambling into unrelated content. Checks each phrase in
    `refusal_phrases` and, for whichever appears EARLIEST in the text, cuts
    everything after the sentence containing it.
    """
    phrases = refusal_phrases or REFUSAL_PHRASE_VARIANTS
    lower = text.lower()

    earliest_idx = None
    earliest_end = None

    for phrase in phrases:
        idx = lower.find(phrase)
        if idx != -1 and (earliest_idx is None or idx < earliest_idx):
            earliest_idx = idx
            earliest_end = idx + len(phrase)

    if earliest_idx is None:
        return text

    sentence_end = text.find(".", earliest_end)
    if sentence_end == -1:
        sentence_end = len(text)
    else:
        sentence_end += 1

    return text[:sentence_end].strip()


# ── retrieval fusion / gating ────────────────────────────────────────────

def rrf_merge(*rankings: list, k: int = 60) -> dict:
    """Reciprocal Rank Fusion over any number of ranked lists."""
    scores: dict = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return scores


def passes_retrieval_gate(results: list[dict], threshold: float = 0.5) -> bool:
    """
    True if the top reranked chunk meets the relevance threshold.
    Expects results sorted best-first with a sigmoid-calibrated
    'rerank_score' in [0,1] (0.5 = the reranker's own relevance boundary).
    """
    if not results:
        return False
    return results[0].get("rerank_score", 0.0) >= threshold


def retrieval_confidence_band(results: list[dict], gate_threshold: float = 0.5,
                               ambiguous_ceiling: float = 0.65) -> str:
    """
    Classify retrieval confidence into one of three bands:

      "confident"  — top score well above the gate, proceed normally
      "ambiguous"  — top score barely clears the gate AND multiple
                     distinct sources are competing near the top, OR the
                     query plausibly matches more than one topic in the
                     corpus. Worth asking the user to narrow down rather
                     than guessing.
      "none"       — failed the gate entirely, refuse.

    This backs the "ask a clarifying question instead of guessing" feature:
    a continuous confidence signal rather than a binary pass/fail, used to
    decide whether to answer, ask, or refuse.
    """
    if not results or not passes_retrieval_gate(results, gate_threshold):
        return "none"

    top_score = results[0].get("rerank_score", 0.0)
    if top_score >= ambiguous_ceiling:
        return "confident"

    # Borderline score: check whether the top few results are scattered
    # across multiple distinct sources/sections — a sign the query could
    # plausibly mean more than one thing in this corpus.
    top_sources = {r.get("source") for r in results[:4] if r.get("source")}
    if len(top_sources) >= 3:
        return "ambiguous"

    # Borderline score but results agree on source/topic — treat as
    # confident-enough rather than interrupting the user unnecessarily.
    return "confident"


# ── conversational query rewriting ───────────────────────────────────────

FOLLOW_UP_MARKERS = [
    "more", "tell me more", "what about", "and", "also", "that", "those",
    "it", "them", "this", "these", "why", "how", "further", "elaborate",
    "step", "from step", "continue", "next",
]


def looks_like_followup(query: str, max_standalone_words: int = 10) -> bool:
    """
    True if `query` is short and/or pronoun-heavy enough that it likely
    can't be resolved on its own and needs the previous turn's context
    (e.g. "give me that from step 1", "what about the tablet?").
    """
    q = query.lower().strip()
    if len(q.split()) <= max_standalone_words:
        return True
    return any(marker in q.split() for marker in FOLLOW_UP_MARKERS)


def build_retrieval_query(current_query: str, previous_query: str | None) -> str:
    """
    Build a richer query string for the RETRIEVAL step (not the LLM
    prompt) when the current query looks like a follow-up.

    FIX: previously a follow-up like "give me that from step 1" was sent
    to retrieval completely on its own — almost no lexical/semantic
    signal, so it failed the retrieval gate outright even when the
    previous turn's topic ("how to connect hub to MyConnect app") was
    sitting right there in memory. Concatenating the two gives retrieval
    something to actually match against.

    Deliberately does NOT include the assistant's prior ANSWER text — only
    the prior user query — to avoid injecting noisy/hallucinated content
    into the retrieval signal.
    """
    if not previous_query or not looks_like_followup(current_query):
        return current_query

    return f"{previous_query} {current_query}".strip()