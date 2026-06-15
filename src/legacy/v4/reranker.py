import logging
from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)

_model = None


def _get():
    global _model
    if _model is None:
        _model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _model


def rerank(query, chunks, top_k=3):
    if not chunks:
        return []

    try:
        model = _get()
        pairs = [(query, c["text"]) for c in chunks]
        scores = model.predict(pairs)

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