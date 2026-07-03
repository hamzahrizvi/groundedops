"""
Pure text-processing utilities — no ML dependencies.

Extracted from grounding.py and llm.py so this logic can be unit-tested
without pulling in sentence-transformers or hitting Ollama/DeepSeek.
"""

import re

REFUSAL_PHRASE = "i could not find that in the knowledge base"

# Small local models often refuse correctly but in non-canonical wording,
# then continue rambling (queries 9/11: "I'm sorry, but there is no
# information about X inside the given context. In a hypothetical
# scenario, you are an IoT engineer..."). The exact prompted phrase
# (REFUSAL_PHRASE) catches the *intended* wording; these catch the most
# common deviations so the post-refusal ramble still gets cut.
REFUSAL_PHRASE_VARIANTS = [
    REFUSAL_PHRASE,
    "there is no information about",
    "the context does not contain",
    "does not contain this information",
    "no information about this in the",
]

MIN_UNIT_LEN   = 12


def split_units(answer: str, min_len: int = MIN_UNIT_LEN) -> list[str]:
    """
    Split an answer into checkable units for grounding verification.

    Handles both prose (split on sentence boundaries) and
    numbered/bulleted lists (split on newlines + strip list markers).

    Example:
        "1. Connect the tablet\\n2. Open the app"
        → ["Connect the tablet", "Open the app"]
    """
    lines = [l.strip() for l in answer.split("\n") if l.strip()]

    units = []
    for line in lines:
        # Strip leading list markers: "1.", "1)", "-", "*"
        cleaned = re.sub(r"^(\d+[\.\)]|\-|\*)\s*", "", line)

        # Further split prose lines on sentence boundaries
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        for s in sentences:
            s = s.strip().rstrip(".")
            if len(s) >= min_len:
                units.append(s)

    return units


def rrf_merge(*rankings: list, k: int = 60) -> dict:
    """
    Reciprocal Rank Fusion: merge any number of ranked lists (best-first,
    items can be any hashable type — e.g. chunk IDs) into a single score
    dict. An item's score is the sum of 1/(k+rank+1) across every ranking
    it appears in, so items appearing near the top of multiple rankings
    score highest, but an item appearing in only ONE ranking still gets
    a non-zero score and remains a candidate.
    """
    scores: dict = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return scores


def passes_retrieval_gate(results: list[dict], threshold: float = 0.5) -> bool:
    """
    True if the top retrieved/reranked chunk meets the relevance threshold.

    Expects results sorted best-first, each with a 'rerank_score' in [0,1]
    (see reranker.py — scores are sigmoid-calibrated, so 0.5 is the
    reranker model's own relevance/irrelevance boundary).

    This is the "robust grounding without brittle keyword rules" check:
    a continuous relevance score evaluated BEFORE generation, rather than
    pattern-matching the LLM's output afterward.
    """
    if not results:
        return False
    return results[0].get("rerank_score", 0.0) >= threshold


def truncate_after_refusal(
    text: str,
    refusal_phrases: list[str] | None = None,
) -> str:
    """
    Small local models sometimes emit a correct(-ish) refusal sentence and
    then continue rambling into unrelated content (hypothetical scenarios,
    riddles, etc.) Checks each phrase in `refusal_phrases` (default:
    REFUSAL_PHRASE_VARIANTS) and, for whichever appears EARLIEST in the
    text, cuts everything after the sentence containing it.

    Example:
        "I'm sorry, but there is no information about the capital of
        France inside the given context. In a hypothetical scenario,
        you are an IoT engineer..."
        → "I'm sorry, but there is no information about the capital of
        France inside the given context."
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
        sentence_end += 1  # include the period

    return text[:sentence_end].strip()