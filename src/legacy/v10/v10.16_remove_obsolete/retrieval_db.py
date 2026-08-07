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


def _invalidate_bm25_cache():
    """Force BM25 rebuild on next query. Needed after a re-tag/reassign,
    where chunk COUNT is unchanged so the count-based cache wouldn't
    otherwise refresh the metadata it holds."""
    _bm25_cache["count"] = -1


def _get_bm25_index(collection):
    count = collection.count()

    with _bm25_lock:
        if _bm25_cache["count"] == count and _bm25_cache["index"] is not None:
            return _bm25_cache["index"], _bm25_cache["chunks"]

        data = collection.get(include=["documents", "metadatas"])
        chunks = [
            {
                "id": i,
                "text": d,
                "source": m.get("source", "unknown"),
                "product": m.get("product", ""),
                "category": m.get("category", ""),
            }
            for i, d, m in zip(data["ids"], data["documents"], data["metadatas"])
            # v10.16: doc2query removed. Fresh ingests no longer create
            # kind="query" entries, but a collection built before this
            # change may still hold them. Drop them here so leftover
            # synthetic questions never enter BM25 or surface as results;
            # a re-ingest clears them permanently.
            if m.get("kind", "chunk") != "query"
        ]

        corpus = [c["text"].lower().split() for c in chunks]
        index = BM25Okapi(corpus) if corpus else None

        _bm25_cache["count"] = count
        _bm25_cache["index"] = index
        _bm25_cache["chunks"] = chunks

        return index, chunks


def _matches_scope(meta: dict, source_filter: str | None,
                   scope: dict | None) -> bool:
    """v10.5: scope by EXPLICIT tags set at upload — no filename guessing.
    scope = {"product": key} matches chunks tagged with that product;
    scope = {"category": key} matches any chunk in that category. The
    source_filter still supports the single-document 'ask about this doc'
    flow. None scope = unscoped (whole corpus)."""
    if source_filter and meta.get("source") != source_filter:
        return False
    if scope:
        if "product" in scope:
            return meta.get("product") == scope["product"]
        if "category" in scope:
            return meta.get("category") == scope["category"]
    return True


def _bm25_ranking(query: str, collection, limit: int, source_filter: str | None,
                  scope: dict | None = None) -> list[str]:
    index, chunks = _get_bm25_index(collection)
    if index is None or not chunks:
        return []

    scores = index.get_scores(query.lower().split())
    order = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)

    ids = []
    for i in order:
        if not _matches_scope(chunks[i], source_filter, scope):
            continue
        ids.append(chunks[i]["id"])
        if len(ids) >= limit:
            break
    return ids


def _dense_ranking(query: str, collection, limit: int, source_filter: str | None,
                   scope: dict | None = None) -> list[str]:
    q_vec = embed_query(query)
    # v10.5: exact-match metadata `where` (valid in Chroma, unlike the old
    # substring attempt). scope = {"product": key} or {"category": key},
    # set directly at upload time, so this filters server-side, fast and
    # exact — no over-fetch/post-filter, no filename matching.
    where = None
    if source_filter:
        where = {"source": source_filter}
    elif scope and "product" in scope:
        where = {"product": scope["product"]}
    elif scope and "category" in scope:
        where = {"category": scope["category"]}
    if where:
        res = collection.query(query_embeddings=[q_vec.tolist()],
                               n_results=limit, where=where)
        return res["ids"][0] if res.get("ids") else []
    res = collection.query(query_embeddings=[q_vec.tolist()], n_results=limit)
    return res["ids"][0] if res.get("ids") else []


def retrieve_from_db(
    query: str,
    top_k: int = 10,
    source_filter: str | None = None,
    scope: dict | None = None,
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

    bm25_ids = _bm25_ranking(query, collection, fetch_n, source_filter, scope)
    dense_ids = _dense_ranking(query, collection, fetch_n, source_filter, scope)

    scores = rrf_merge(bm25_ids, dense_ids, k=RRF_K)
    if not scores:
        return []

    # Keep a margin of candidates (top_k*2) so the downstream reranker has
    # room to reorder before the answering pipeline trims to top_k.
    ranked_ids = sorted(scores, key=scores.get, reverse=True)[:top_k * 2]

    _, chunks = _get_bm25_index(collection)
    by_id = {c["id"]: c for c in chunks}

    # v10.16: with doc2query gone, every ranked id maps directly to a real
    # chunk (no question->parent indirection, no dedupe needed). Any id the
    # dense query returns that isn't in by_id — e.g. a stale kind="query"
    # entry filtered out above — is skipped by the `if not entry` guard.
    results = []
    for doc_id in ranked_ids:
        entry = by_id.get(doc_id)
        if not entry:
            continue
        results.append({
            "id": entry["id"],
            "text": entry["text"],
            "source": entry["source"],
            "retrieval_score": round(scores[doc_id], 6),
        })
        if len(results) >= top_k:
            break

    return results
