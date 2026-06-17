"""
Hybrid retrieval: BM25 (full corpus) + dense (ChromaDB) merged via RRF.

Each ranking is computed INDEPENDENTLY over the full corpus, so a chunk
that's a strong keyword match but a weak embedding match (or vice versa)
can still surface — a chunk ranking #1 on BM25 but absent from dense
results entirely is not invisible to the merge.
"""

import threading
from rank_bm25 import BM25Okapi

from embeddings import embed_query
from db import get_collection
from text_utils import rrf_merge

RRF_K = 60

_bm25_lock = threading.Lock()
_bm25_cache = {"count": -1, "index": None, "chunks": None}


def _get_bm25_index(collection):
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
        index = BM25Okapi(corpus) if corpus else None

        _bm25_cache["count"] = count
        _bm25_cache["index"] = index
        _bm25_cache["chunks"] = chunks

        return index, chunks


def _bm25_ranking(query: str, collection, limit: int, source_filter: str | None) -> list[str]:
    index, chunks = _get_bm25_index(collection)
    if index is None or not chunks:
        return []

    scores = index.get_scores(query.lower().split())
    order = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)

    ids = []
    for i in order:
        if source_filter and chunks[i]["source"] != source_filter:
            continue
        ids.append(chunks[i]["id"])
        if len(ids) >= limit:
            break
    return ids


def _dense_ranking(query: str, collection, limit: int, source_filter: str | None) -> list[str]:
    q_vec = embed_query(query)
    kwargs = {"query_embeddings": [q_vec.tolist()], "n_results": limit}
    if source_filter:
        kwargs["where"] = {"source": source_filter}

    res = collection.query(**kwargs)
    return res["ids"][0] if res.get("ids") else []


def retrieve_from_db(
    query: str,
    top_k: int = 10,
    source_filter: str | None = None,
) -> list[dict]:
    """
    Hybrid retrieval over the full corpus, merged via RRF.

    source_filter, if given, scopes BOTH rankings to chunks from that one
    source filename — used by the "ask more about this document" flow
    triggered from a clickable source in the UI.

    Returns chunk dicts with 'id', 'text', 'source', 'retrieval_score'.
    The 'id' is the stable ChromaDB id, used downstream to fetch full
    chunk content on demand (clickable sources) without re-querying.
    """
    collection = get_collection()
    n = collection.count()
    if n == 0:
        return []

    fetch_n = min(max(top_k * 2, top_k), n)

    bm25_ids = _bm25_ranking(query, collection, fetch_n, source_filter)
    dense_ids = _dense_ranking(query, collection, fetch_n, source_filter)

    scores = rrf_merge(bm25_ids, dense_ids, k=RRF_K)
    if not scores:
        return []

    ranked_ids = sorted(scores, key=scores.get, reverse=True)[:top_k]

    _, chunks = _get_bm25_index(collection)
    by_id = {c["id"]: c for c in chunks}

    results = []
    for doc_id in ranked_ids:
        chunk = by_id.get(doc_id)
        if chunk:
            results.append({
                "id": chunk["id"],
                "text": chunk["text"],
                "source": chunk["source"],
                "retrieval_score": round(scores[doc_id], 6),
            })

    return results