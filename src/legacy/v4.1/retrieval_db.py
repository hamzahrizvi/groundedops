"""
Hybrid retrieval: BM25 (full corpus) + dense (ChromaDB) merged via RRF.

PREVIOUS BEHAVIOUR (bug): BM25 was run only on the dense top-10, i.e. it
could only re-sort candidates dense retrieval already found. A chunk
that is a strong keyword match (e.g. "MyCheckr", "multicast" — terms the
all-MiniLM-L6-v2 embedding model has never seen) but a weak embedding
match could sit outside the dense top-10 and be permanently invisible
to BM25, defeating the point of "hybrid" retrieval.

FIX: BM25 and dense are now computed INDEPENDENTLY over the FULL corpus,
each producing their own ranked candidate list, then merged with
Reciprocal Rank Fusion (RRF). A chunk that ranks #1 on BM25 but #80 on
dense can still surface in the merged top-k.

PERFORMANCE: building a BM25 index requires the full corpus. We cache
the index and invalidate it when collection.count() changes (i.e. after
ingest or reset). This is a simple heuristic — it won't detect in-place
edits that preserve the chunk count — but is correct for this project's
ingest/reset-only mutation pattern. For large corpora (>10k chunks) a
persisted/incremental BM25 index would be needed; see README.
"""

import threading
from rank_bm25 import BM25Okapi

from embeddings import embed_query
from db import get_collection
from text_utils import rrf_merge

RRF_K = 60

_bm25_lock  = threading.Lock()
_bm25_cache = {"count": -1, "index": None, "chunks": None}


def _get_bm25_index(collection):
    """Return (BM25Okapi index, list of chunk dicts with stable 'id' field)."""
    count = collection.count()

    with _bm25_lock:
        if _bm25_cache["count"] == count and _bm25_cache["index"] is not None:
            return _bm25_cache["index"], _bm25_cache["chunks"]

        data = collection.get(include=["documents", "metadatas"])
        chunks = [
            {"id": i, "text": d, "source": m.get("source", "unknown")}
            for i, d, m in zip(data["ids"], data["documents"], data["metadatas"])
        ]

        corpus = [c["text"].lower().split() for c in chunks]
        index  = BM25Okapi(corpus) if corpus else None

        _bm25_cache["count"]  = count
        _bm25_cache["index"]  = index
        _bm25_cache["chunks"] = chunks

        return index, chunks


def _bm25_ranking(query: str, collection, limit: int) -> list[str]:
    """Return chunk ids ranked by BM25 score, best first."""
    index, chunks = _get_bm25_index(collection)
    if index is None or not chunks:
        return []

    scores = index.get_scores(query.lower().split())
    order  = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
    return [chunks[i]["id"] for i in order[:limit]]


def _dense_ranking(query: str, collection, limit: int) -> list[str]:
    """Return chunk ids ranked by dense similarity, best first."""
    q_vec = embed_query(query)
    res = collection.query(
        query_embeddings=[q_vec.tolist()],
        n_results=limit,
    )
    return res["ids"][0] if res.get("ids") else []


def retrieve_from_db(query: str, top_k: int = 10) -> list[dict]:
    """
    Hybrid retrieval: BM25 + dense, each over the full corpus,
    merged via RRF. Returns chunk dicts with 'text', 'source',
    and 'retrieval_score' (RRF score, for diagnostics).
    """
    collection = get_collection()
    n = collection.count()
    if n == 0:
        return []

    fetch_n = min(max(top_k * 2, top_k), n)

    bm25_ids  = _bm25_ranking(query, collection, fetch_n)
    dense_ids = _dense_ranking(query, collection, fetch_n)

    scores = rrf_merge(bm25_ids, dense_ids, k=RRF_K)
    if not scores:
        return []

    ranked_ids = sorted(scores, key=scores.get, reverse=True)[:top_k]

    # Look up text/source from the BM25 cache (already holds the full corpus)
    _, chunks = _get_bm25_index(collection)
    by_id = {c["id"]: c for c in chunks}

    results = []
    for doc_id in ranked_ids:
        chunk = by_id.get(doc_id)
        if chunk:
            results.append({
                "text":            chunk["text"],
                "source":          chunk["source"],
                "retrieval_score": round(scores[doc_id], 6),
            })

    return results