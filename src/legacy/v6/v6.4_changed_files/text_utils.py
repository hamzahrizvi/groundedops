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
# splitter intact (see fix_camel_case below).
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


# Known chat-template/system-prompt boilerplate that local models
# (especially phi via Ollama) occasionally emit verbatim or near-
# verbatim when the retrieved context is too thin to actually answer
# from — e.g. "MyConnect System is a chat between a curious user and
# an artificial intelligence assistant. The assistant gives helpful
# answers..." for "introduction of myconnect system". This is the
# default Vicuna-style system message baked into several Ollama model
# templates, not anything our own prompt contains (grepped — not in
# this codebase), so it can only be coming from the model itself.
#
# Why this needs its own check rather than relying on the NLI grounding
# check: this kind of generic, low-content boilerplate makes no
# specific factual claim, so an NLI entailment model has nothing
# concrete to contradict — it scored 0.934 (well above the 0.35
# threshold) in production despite having zero actual relationship to
# the retrieved context. Pattern-matching known leak phrases, the same
# approach already used for is_refusal/REFUSAL_PHRASE_VARIANTS, is a
# deterministic catch for a deterministic failure mode that a semantic
# similarity/entailment check isn't designed to catch.
TEMPLATE_LEAK_PHRASES = [
    "curious user and an artificial intelligence assistant",
    "i am an ai language model",
    "i am a large language model",
    "as an ai language model",
    "i'm an ai assistant",
    "i don't have personal",
    "as a language model",
]


def is_template_leak(answer: str, phrases: list[str] | None = None) -> bool:
    """True if `answer` contains known model chat-template/system-prompt
    boilerplate rather than an actual answer derived from context."""
    phrases = phrases or TEMPLATE_LEAK_PHRASES
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
    Classify retrieval confidence into one of three bands: "confident",
    "ambiguous" (borderline score AND results scattered across several
    distinct sources — worth asking the user to narrow down), or "none"
    (failed the gate, refuse).
    """
    if not results or not passes_retrieval_gate(results, gate_threshold):
        return "none"

    top_score = results[0].get("rerank_score", 0.0)
    if top_score >= ambiguous_ceiling:
        return "confident"

    top_sources = {r.get("source") for r in results[:4] if r.get("source")}
    if len(top_sources) >= 3:
        return "ambiguous"

    return "confident"


# ── conversational query condensation (Rewrite-Retrieve-Read) ───────────
#
# REPLACES the previous approach, which tried to GUESS whether a query
# was a "follow-up" using surface heuristics (word count <= 10, or
# membership in a fixed pronoun/keyword list). That heuristic flagged
# essentially every short, complete, self-contained question as a
# follow-up — e.g. "how to connect tablet to hub" (6 words) — causing it
# to be silently concatenated with whatever unrelated query happened to
# run before it in the same (unscoped, never-cleared) memory buffer.
#
# The replacement follows the "Rewrite-Retrieve-Read" pattern (Ma et al.,
# 2023, arXiv:2305.14283) used by LangChain's history-aware retriever and
# documented across multiple production conversational-RAG write-ups: a
# fast LLM call is ALWAYS given the conversation history and the current
# message, and is explicitly instructed to return the message UNCHANGED
# if it's already self-contained, or rewritten into a standalone query if
# it depends on prior context. The decision and the fix happen in the
# same step — there is no separate, brittle "is this a follow-up?"
# classifier to get wrong.
#
# This module only contains the PURE, unit-testable pieces (prompt
# construction and output parsing). The actual model call lives in
# llm.condense_query, which calls these.

CONDENSE_PROMPT_TEMPLATE = """You are a query rewriting assistant for a document search system.

Conversation history:
{history_text}

Latest user message: "{current_query}"

Task: If the latest message depends on the conversation history to make sense (for example it uses words like "that", "it", "more", "step 1", or is otherwise incomplete on its own), rewrite it into a single, self-contained search query that includes the necessary context from the history.

If the latest message is ALREADY a complete, self-contained question that does not depend on the history, return it EXACTLY AS-IS, unchanged.

Output ONLY the final query text. No explanation, no preamble, no quotation marks.

Rewritten query:"""


def build_condense_prompt(
    current_query: str,
    history: list[dict],
    max_history_turns: int = 2,
) -> str:
    """
    Build the prompt for LLM-based query condensation. Only the most
    recent `max_history_turns` are included — older turns are rarely
    needed to resolve an immediate follow-up, and keeping this prompt
    short matters since it runs as an extra latency-sensitive call on
    every turn beyond the first.
    """
    recent = history[-max_history_turns:] if history else []

    if recent:
        history_text = "\n".join(
            f'User: {h["q"]}\nAssistant: {h["a"]}' for h in recent
        )
    else:
        history_text = "(none — this is the first message)"

    return CONDENSE_PROMPT_TEMPLATE.format(
        history_text=history_text,
        current_query=current_query,
    )


def parse_condense_output(raw_output: str, fallback_query: str) -> str:
    """
    Clean up the LLM's rewritten-query output. Falls back to the
    original query if the output is empty or clearly degenerate.

    BUG FIXED: phi tends to continue generating past the rewritten
    query and outputs the rest of the prompt template (rules, examples,
    etc.) as additional lines. Taking only the FIRST non-empty line
    prevents the entire prompt from leaking into the resolved_query
    field and downstream retrieval.
    """
    if not raw_output:
        return fallback_query

    # Take only the first non-empty line — the rewritten query is
    # always a single line; everything after is phi continuing the prompt
    first_line = next((l.strip() for l in raw_output.split("\n") if l.strip()), "")
    if not first_line:
        return fallback_query

    text = first_line.strip("\"'")
    text = re.sub(r"^(rewritten query|query)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = text.strip().strip("\"'")

    if not text:
        return fallback_query

    return text


# Targeted anaphora/ellipsis markers — specific linguistic signals that
# a query is referencing prior context rather than being self-contained.
# Used to guard the condense_query model call so it's only invoked when
# there's actual evidence of dependency on prior turns, NOT as a proxy
# for query length or complexity (which was the old broken heuristic).
_REFERENCE_PATTERNS = [
    re.compile(r"^(more|elaborate|continue|further|tell me more)\b", re.IGNORECASE),
    re.compile(r"\b(step \d+|from step|from above|the above|as above|as mentioned|from that)\b", re.IGNORECASE),
    re.compile(r"^(and |also |but )\b", re.IGNORECASE),
    re.compile(r"\b(give me that|show me that|what about that|more about that|more context|more detail)\b", re.IGNORECASE),
    re.compile(r"^\s*(it|they|them|those|these)\b", re.IGNORECASE),
    re.compile(r"\bi need more context\b", re.IGNORECASE),
]


# Generic domain vocabulary for this corpus (MyConnect/MyCheckr
# installation, networking, and registration docs). Deliberately broad
# and topic-level rather than product-specific — the point is NOT to
# match a specific manual, just to tell apart "this query is clearly
# about something in our domain but underspecified" (e.g. "explain why
# device registration might fail" — which device? MyCheckr? the Hub?)
# from "this query has nothing to do with our domain at all" (e.g.
# "what is the capital of france").
_DOMAIN_VOCABULARY = {
    "device", "devices", "hub", "tablet", "mycheckr", "myconnect",
    "install", "installation", "installer", "verify", "verification",
    "registration", "register", "system", "network", "wifi", "app",
    "connect", "connection", "connected", "power", "relay", "log",
    "alert", "configure", "configured", "checklist", "sign", "signoff",
    "firmware", "multicast", "discovery", "ethernet",
}


def has_domain_vocabulary(query: str) -> bool:
    """
    True if `query` contains at least one term from this corpus's
    domain vocabulary, even if retrieval couldn't actually find a good
    match for it. Used in main.py's "none" confidence branch to decide
    between asking a clarifying question (vague-but-in-domain) and a
    flat rejection (genuinely out-of-domain, e.g. "capital of France").
    """
    tokens = {re.sub(r"[^\w]", "", t).lower() for t in query.split()}
    return any(t in _DOMAIN_VOCABULARY for t in tokens)


def has_reference_markers(query: str) -> bool:
    """
    True if the query contains specific linguistic signals that it is
    referencing prior conversation context (pronouns, 'step N', 'from
    above', 'give me that', continuation conjunctions, etc.) and may
    therefore need to be rewritten into a standalone query.

    Returns False for queries that are clearly self-contained, which
    short-circuits the condense_query model call entirely and prevents
    phi from incorrectly rewriting standalone queries like "post
    installation verification installer sign off" into whatever topic
    happened to be discussed in the previous turn.
    """
    return any(p.search(query) for p in _REFERENCE_PATTERNS)


def is_followup_turn(raw_query: str, history: list, resolved_query: str) -> bool:
    """
    True if this turn was dependent on conversation history rather than a
    fresh, standalone question — either the raw query had reference
    markers, or condense_query actually rewrote it into something
    different.

    Used in main.py's retrieval_confidence_band == "none" branch to tell
    apart two cases that look identical from a bare retrieval score but
    are NOT the same situation:
      - a genuinely out-of-domain standalone query (e.g. "capital of
        France") — the flat "I could not find that" rejection is correct.
      - an in-context follow-up whose condensed/rewritten query still
        failed to retrieve anything (e.g. "is there anything else, I
        checked the above and they're fine") — flatly rejecting this the
        same way breaks the conversational flow; asking a clarifying
        question is the right response instead.

    Requires `history` to be non-empty: with no prior turns there is
    nothing to be a follow-up to, regardless of surface phrasing (a
    first-ever message containing "the above" with no history is just a
    malformed standalone query, not a follow-up).
    """
    if not history:
        return False
    return has_reference_markers(raw_query) or resolved_query != raw_query


# ── semantic query routing ────────────────────────────────────────────
#
# REPLACES keyword-list classification (router.py used to check whether
# any of a fixed set of strings like "why", "how does", "checklist"
# appeared in the query). That has the same structural weakness the old
# follow-up detector had: phrasing that doesn't happen to contain one of
# the listed words/phrases gets misclassified regardless of actual
# intent, and it's brittle to paraphrasing ("what's the reason device
# registration fails" contains none of the _REASONING_KW strings even
# though it's clearly asking "why").
#
# Replacement: embed a small set of canonical example queries per role
# ONCE (router.py does this, cached), embed the incoming query with the
# SAME embedding model already loaded for retrieval (no extra model
# load, no extra LLM call), and classify by nearest-neighbor cosine
# similarity. This generalizes to paraphrasing in a way keyword lists
# structurally cannot.
#
# This function is the pure, ML-free piece: given already-computed
# vectors (numpy arrays), do the actual classification math. The
# embedding-model calls live in router.py and require sentence-
# transformers, which isn't available in this sandbox — see
# tests/test_router.py for the documented verification boundary.

def classify_by_similarity(
    query_vec,
    category_vectors: dict,
    min_confidence: float = 0.30,
    default: str = "accurate",
) -> str:
    """
    Classify `query_vec` against `category_vectors` (role -> list of
    example-query vectors, all pre-normalized) by cosine similarity.

    Since vectors are expected pre-normalized (unit length — this is
    what embeddings.embed_query/embed_texts already produce), cosine
    similarity is just a dot product.

    For each category, uses the BEST-matching example (max similarity)
    rather than the average — a query only needs to closely resemble
    ONE good canonical example of a category to belong to it; averaging
    would penalize categories with more diverse examples for no good
    reason.

    Falls back to `default` if no category's best match clears
    `min_confidence` — i.e. the query doesn't closely resemble ANY
    canonical example of ANY specific category, so the safe behaviour is
    the general-purpose role rather than a confident wrong guess.
    """
    if not category_vectors:
        return default

    best_role = default
    best_score = min_confidence

    for role, vectors in category_vectors.items():
        if not vectors:
            continue
        for v in vectors:
            score = float(query_vec @ v)
            if score > best_score:
                best_score = score
                best_role = role

    return best_role
