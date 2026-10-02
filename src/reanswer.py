"""Re-answer a question by re-reading the passages it already retrieved.

WHY THIS AND NOT A BETTER RERANKER. Measured on the 19-case retrieval suite
(tools/bench_reranker.py), over three cross-encoders:

    model                         r@1   r@3   r@8    sec/q
    ms-marco-MiniLM-L-6-v2 (now)  68%   94%   100%    2.0
    ms-marco-MiniLM-L-12-v2       63%   94%    94%    6.0
    BAAI/bge-reranker-base        73%   94%   100%   17.1

Two things follow. The relevant chunk reaches the context 100% of the time
already, so retrieval is not losing answers -- ordering is. And no reranker
fixes that cheaply: the best of them buys five points of r@1 for 8.5x the
latency on a CPU-only box.

But r@3 is 94% across every model. The answer is nearly always among the
top three passages. So the useful question is not "which passage ranks
first" but "which passage actually answers this", and a model reading the
question and the candidates together can decide that in one call -- which is
what a cross-encoder cannot do, because it scores each passage in isolation
and never sees them side by side.

TWO STRATEGIES, in order:
  select  ask the model which passages answer the question, and answer from
          exactly those. Handles the observed failure directly: for "can I
          use an nv9 spectral with note float?" the answer ranked #1 at
          0.9992 and the served answer came from #2.
  merge   if selection returns nothing usable, fall back to the top three
          merged. At 94% r@3 that is a good floor, and it is deterministic.

This module holds only the PURE parts -- prompt construction and output
parsing -- so they are unit-testable without a provider. The model call
lives in main.py.
"""
import re

# Three is not arbitrary: it is where r@3 = 94% sits. Beyond it the curve is
# flat (r@8 is 100%, but eight passages is what the model already failed to
# choose between).
MERGE_TOP_N = 3

# How many candidates the selector is shown. More than CONTEXT_K is
# pointless -- those are the ones that reached the answer in the first place.
SELECT_FROM_N = 8


SELECTION_PROMPT = """You are choosing which source passages answer a question.

Question: {question}

{passages}

Which passages contain information that ANSWERS the question?

Rules:
- A passage that merely mentions the same product or topic does NOT answer the question. A table listing two devices does not answer "can these two work together".
- Prefer a passage that states a fact, a condition or a limitation over one that lists specifications.
- If several passages answer it, list them all.
- If NONE of them answer the question, reply with exactly: NONE

Reply with only the passage numbers, comma separated, or NONE. No explanation.

Passages:"""


def build_selection_prompt(question: str, passages: list[str]) -> str:
    """Number the passages and ask which ones answer the question.

    Numbered from 1 and truncated, because the selector only needs enough of
    each passage to recognise what it is about -- sending the full context
    twice would double the cost of every retry.
    """
    listed = "\n\n".join(
        f"[{i}] {(p or '').strip()[:700]}"
        for i, p in enumerate(passages, 1))
    return SELECTION_PROMPT.format(question=question, passages="") + "\n" + listed


def parse_selection(text: str, n: int) -> list[int]:
    """Zero-based indices of the chosen passages, or [] for NONE/unparseable.

    Deliberately forgiving about wrapping -- a small model asked for "1, 3"
    will sometimes answer "Passages 1 and 3" or "[1][3]" -- but strict about
    range, since a hallucinated [9] against 8 candidates would otherwise
    index into nothing or, worse, wrap around.
    """
    if not text:
        return []
    head = text.strip().splitlines()[0] if text.strip() else ""
    if re.search(r"\bNONE\b", text, re.IGNORECASE) and not re.search(r"\d", head):
        return []
    picked, seen = [], set()
    for m in re.finditer(r"\d+", text):
        v = int(m.group())
        if 1 <= v <= n and v not in seen:
            seen.add(v)
            picked.append(v - 1)
    return picked


def chosen_passages(selection: list[int], ranked: list, merge_top_n: int = MERGE_TOP_N):
    """The passages to answer from: the selection, or the top N merged.

    The fallback is what makes this safe to offer as a button. A selector
    that returns nothing must not produce a worse answer than the one the
    visitor already had -- and the top three are, measurably, where the
    answer is 94% of the time.
    """
    if selection:
        return [ranked[i] for i in selection if 0 <= i < len(ranked)], "selected"
    return ranked[:merge_top_n], "merged_top_%d" % merge_top_n
