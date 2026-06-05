import logging
import numpy as np
from embeddings import embed_query

logger = logging.getLogger(__name__)

RETRIEVAL_THRESHOLD = 0.005


def _bm25_scores(query, chunks):
    try:
        from rank_bm25 import BM25Okapi

        corpus = [c["text"].lower().split() for c in chunks]
        bm25 = BM25Okapi(corpus)
        return np.array(bm25.get_scores(query.lower().split()))

    except ImportError:
        q = set(query.lower().split())
        return np.array([
            len(q & set(c["text"].lower().split())) / max(len(q), 1)
            for c in chunks
        ])


def _dense_scores(query, chunks):
    q_vec = embed_query(query)
    scores = []

    for c in chunks:
        if "embedding" in c:
            scores.append(float(np.dot(q_vec, c["embedding"])))
        else:
            scores.append(0.0)

    return np.array(scores)


def _rrf(a, b, k=60):
    scores = {}

    for r, i in enumerate(a):
        scores[i] = scores.get(i, 0) + 1 / (k + r + 1)

    for r, i in enumerate(b):
        scores[i] = scores.get(i, 0) + 1 / (k + r + 1)

    return scores


def search(query, chunks, top_k=10):
    if not chunks:
        return [], 0.0

    try:
        bm25 = _bm25_scores(query, chunks)
        dense = _dense_scores(query, chunks)

        n = min(len(chunks), top_k * 3)

        b_idx = np.argsort(bm25)[::-1][:n]
        d_idx = np.argsort(dense)[::-1][:n]

        merged = _rrf(b_idx, d_idx)
        ranked = sorted(merged, key=merged.get, reverse=True)[:top_k]

        results = []
        for i in ranked:
            c = dict(chunks[i])
            c["retrieval_score"] = float(merged[i])
            results.append(c)

        top = results[0]["retrieval_score"] if results else 0.0
        return results, top

    except Exception as e:
        logger.error(f"Search failed: {e}")
        return [], 0.0