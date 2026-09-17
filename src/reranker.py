import logging
import os
import torch
from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)

# The reranker decides the final order, and everything downstream gates on
# its score: CONTEXT_FLOOR_RATIO, the retrieval band, the ambiguity guard.
# It was the one stage never upgraded while the embedder moved twice
# (all-MiniLM -> bge-small -> gte-modernbert), so it is now the oldest model
# in the pipeline. Env-overridable so a replacement can be A/B'd against the
# retrieval suite without a code change -- see tools/bench_reranker.py.
#
# Anything CrossEncoder can load works. Measured alternatives and their cost
# on this CPU-only box are in tools/bench_reranker.py's output; pick on that
# evidence rather than on parameter count.
RERANKER_MODEL = os.getenv("RERANKER_MODEL",
                           "cross-encoder/ms-marco-MiniLM-L-6-v2")

_model = None
_loaded = None


def _get(name: str | None = None):
    """Load (and cache) the cross-encoder. Passing `name` swaps the model,
    which is how the benchmark compares several in one process."""
    global _model, _loaded
    want = name or RERANKER_MODEL
    if _model is None or _loaded != want:
        logger.info(f"Loading reranker: {want}")
        _model = CrossEncoder(want)
        _loaded = want
    return _model


def rerank(query, chunks, top_k=3):
    """
    Rerank chunks by relevance to the query.

    rerank_score is squashed through a sigmoid, giving a [0,1] score
    where 0.5 is the model's own decision boundary (raw logit == 0).
    This makes the score meaningful as a confidence threshold in main.py's
    retrieval gate — raw cross-encoder logits are unbounded and their scale
    varies by model, which makes them hard to threshold sensibly.
    """
    if not chunks:
        return []

    try:
        model = _get()
        pairs = [(query, c["text"]) for c in chunks]
        scores = model.predict(pairs, activation_fn=torch.nn.Sigmoid())

        ranked = sorted(zip(scores, chunks), reverse=True, key=lambda x: x[0])

        out = []
        for s, c in ranked[:top_k]:
            c = dict(c)
            c["rerank_score"] = float(s)
            out.append(c)

        return out

    except Exception as e:
        logger.error(f"Reranker failed: {e}")
        return chunks[:top_k]
