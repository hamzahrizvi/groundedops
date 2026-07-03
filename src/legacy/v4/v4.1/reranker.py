import logging
import torch
from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)

_model = None


def _get():
    global _model
    if _model is None:
        _model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
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