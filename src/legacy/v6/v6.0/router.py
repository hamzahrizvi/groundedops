"""
Query router.

Classifies an incoming query into one of four roles:
  extract   → structured list/checklist extraction  → mistral
  fast      → short factual lookup                  → phi  (mistral fallback)
  accurate  → multi-sentence explanation             → mistral (deepseek fallback)
  reasoning → causal / comparative / multi-hop       → mistral (deepseek fallback)

The fallback chain is enforced in llm.generate_with_fallback().

CLASSIFICATION METHOD — semantic, not keyword-list:

Previously this module checked whether any of a fixed set of strings
("why", "how does", "checklist", "give me", ...) appeared in the query.
That has the same structural weakness as the old word-count follow-up
detector this project already fixed once: phrasing that doesn't happen
to contain one of the listed words gets misclassified regardless of
actual intent. "What's the reason device registration fails" contains
none of the old _REASONING_KW strings despite obviously being a "why"
question; "list the steps to power on the hub" contains none of the old
_EXTRACT_KW strings despite obviously being a checklist request.

Replacement: a small set of canonical EXAMPLE queries is embedded once
per role (cached after first use), using the SAME sentence-transformers
model already loaded for retrieval (embeddings.py) — no extra model
load, no extra LLM call. The incoming query is embedded and classified
by nearest-neighbor cosine similarity against those examples (the pure
math lives in text_utils.classify_by_similarity, fully unit-tested
without needing sentence-transformers installed — see
tests/test_router.py for the documented verification boundary: the
classification LOGIC is tested directly with synthetic vectors, but the
actual embedding model's real-world accuracy can only be confirmed with
sentence-transformers installed and a live run, which this sandbox
doesn't have).

This generalizes to paraphrasing in a way a keyword list structurally
cannot, and degrades gracefully: if the embedding model can't be loaded
for any reason, routing falls back to "accurate" (the safest general-
purpose role) rather than crashing the query pipeline.
"""

import logging
import threading

from text_utils import classify_by_similarity

logger = logging.getLogger(__name__)

# Primary (provider, model) per role
MODEL_MAP: dict[str, tuple[str, str]] = {
    "extract":   ("local", "mistral"),
    "fast":      ("local", "phi"),
    "accurate":  ("local", "mistral"),
    "reasoning": ("local", "mistral"),   # deepseek used as fallback via llm.py
}

# Minimum cosine similarity to a category's best-matching example before
# that category is trusted over the "accurate" default. Below this, the
# query doesn't closely resemble any canonical example of any specific
# role, so the safe behaviour is the general-purpose role rather than a
# confident wrong guess.
MIN_ROUTE_CONFIDENCE = 0.45

# Canonical example queries per role. Deliberately varied in phrasing
# (not just templated substitutions of the same sentence) so the
# embedding space actually has to generalize, not just pattern-match a
# near-identical string.
CATEGORY_EXAMPLES: dict[str, list[str]] = {
    "reasoning": [
        "why is multicast required for hub discovery",
        "explain why device registration might fail",
        "what's the reason the relay doesn't trigger",
        "what causes the tablet to lose connection",
        "how does myconnect work with mycheckr",
        "what is the relationship between the hub and the app",
        "what happens when the network connection drops",
        "compare the basic and advanced verification checklists",
        "what is the impact of disabling multicast",
        "why would device registration be rejected",
    ],
    "extract": [
        "give me the checklist before leaving site after installation",
        "how to connect tablet to hub",
        "give me steps to install and verify system is working",
        "what are the steps to register a new device",
        "list the steps to power on the hub",
        "walk me through configuring device rules",
        "show me the installation procedure",
        "what is the sign-off checklist for installers",
        "how do I assign relay actions",
        "steps to mount the hub on the wall",
    ],
    "fast": [
        "what is the hub ip",
        "default login credentials for myconnect",
        "what is mycheckr",
        "what is the default password",
        "what version is the myconnect app",
        "what is the name of the relay output",
        "define mycheckr",
        "what does the hub do",
        "introduction of myconnect system",
        "what is the ip address of the hub",
    ],
}

_lock = threading.Lock()
_category_vectors: dict[str, list] | None = None
_load_failed = False


def _get_category_vectors():
    """
    Lazily embed every canonical example once, cached for the life of
    the process. Returns None (rather than raising) if the embedding
    model can't be loaded, so route_model can fall back gracefully
    instead of taking down the query pipeline over a routing failure.
    """
    global _category_vectors, _load_failed

    with _lock:
        if _category_vectors is not None or _load_failed:
            return _category_vectors

        try:
            from embeddings import embed_texts

            vectors: dict[str, list] = {}
            for role, examples in CATEGORY_EXAMPLES.items():
                embedded = embed_texts(examples)
                vectors[role] = list(embedded)

            _category_vectors = vectors
            return _category_vectors

        except Exception as e:
            logger.warning(f"Could not load embedding model for semantic routing, "
                          f"falling back to 'accurate' for all queries: {e}")
            _load_failed = True
            return None


def route_model(query: str) -> tuple[str, tuple[str, str]]:
    """
    Returns: (role, (provider, model))

    Usage in main.py:
        role, (provider, model) = route_model(q)
    """
    category_vectors = _get_category_vectors()

    if category_vectors is None:
        role = "accurate"
    else:
        from embeddings import embed_query

        query_vec = embed_query(query)
        role = classify_by_similarity(
            query_vec, category_vectors,
            min_confidence=MIN_ROUTE_CONFIDENCE,
            default="accurate",
        )

    return role, MODEL_MAP[role]
