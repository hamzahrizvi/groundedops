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
            {
                "id": i,
                "text": d,
                "source": m.get("source", "unknown"),
                # doc2query (v8.5): question entries carry their parent
                # chunk's id and text; real chunks have kind="chunk" (or
                # no kind at all for pre-v8.5 collections — treated as
                # chunks, so mixed/old collections keep working).
                "kind": m.get("kind", "chunk"),
                "parent_id": m.get("parent_id"),
                "parent_text": m.get("parent_text"),
            }
            for i, d, m in zip(data["ids"], data["documents"], data["metadatas"])
        ]

        corpus = [c["text"].lower().split() for c in chunks]
        index = BM25Okapi(corpus) if corpus else None

        _bm25_cache["count"] = count
        _bm25_cache["index"] = index
        _bm25_cache["chunks"] = chunks

        return index, chunks


def _matches_scope(source: str, source_filter: str | None,
                   product_sources: list[str] | None) -> bool:
    """A chunk is in scope if it matches the exact source_filter (single-doc
    'ask about this document' flow) AND/OR belongs to the selected product
    (v2.1 — product substring match, many-to-many). None means unscoped."""
    if source_filter and source != source_filter:
        return False
    if product_sources is not None:
        low = source.lower()
        if not any(ps.lower() in low for ps in product_sources):
            return False
    return True


def _bm25_ranking(query: str, collection, limit: int, source_filter: str | None,
                  product_sources: list[str] | None = None) -> list[str]:
    index, chunks = _get_bm25_index(collection)
    if index is None or not chunks:
        return []

    scores = index.get_scores(query.lower().split())
    order = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)

    ids = []
    for i in order:
        if not _matches_scope(chunks[i]["source"], source_filter, product_sources):
            continue
        ids.append(chunks[i]["id"])
        if len(ids) >= limit:
            break
    return ids


def _dense_ranking(query: str, collection, limit: int, source_filter: str | None,
                   product_sources: list[str] | None = None) -> list[str]:
    q_vec = embed_query(query)
    # Chroma metadata `where` can't substring-match source filenames, and
    # product->sources is many-to-many, so for product scoping we over-fetch
    # and post-filter in Python via _matches_scope (same rule as BM25).
    # Exact single-source filter still uses the efficient server-side where.
    if source_filter:
        res = collection.query(query_embeddings=[q_vec.tolist()],
                               n_results=limit, where={"source": source_filter})
        return res["ids"][0] if res.get("ids") else []

    over = limit * 5 if product_sources is not None else limit
    res = collection.query(query_embeddings=[q_vec.tolist()], n_results=over,
                           include=["metadatas"])
    if not res.get("ids"):
        return []
    ids, metas = res["ids"][0], res["metadatas"][0]
    out = []
    for cid, meta in zip(ids, metas):
        if _matches_scope(meta.get("source", ""), None, product_sources):
            out.append(cid)
        if len(out) >= limit:
            break
    return out


def retrieve_from_db(
    query: str,
    top_k: int = 10,
    source_filter: str | None = None,
    product_sources: list[str] | None = None,
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

    bm25_ids = _bm25_ranking(query, collection, fetch_n, source_filter, product_sources)
    dense_ids = _dense_ranking(query, collection, fetch_n, source_filter, product_sources)

    scores = rrf_merge(bm25_ids, dense_ids, k=RRF_K)
    if not scores:
        return []

    ranked_ids = sorted(scores, key=scores.get, reverse=True)[:top_k * 2]

    _, chunks = _get_bm25_index(collection)
    by_id = {c["id"]: c for c in chunks}

    # doc2query mapping (v8.5): a hit on a generated QUESTION entry is
    # credit for its PARENT chunk — the answering pipeline must only ever
    # see real document text. Map question-hits to parents, then dedupe:
    # if both a question and its parent chunk ranked, keep the better
    # score under the parent's id. Fetch top_k*2 above so dedupe still
    # fills top_k.
    results = []
    seen: dict[str, int] = {}  # effective chunk id -> index in results
    for doc_id in ranked_ids:
        entry = by_id.get(doc_id)
        if not entry:
            continue
        if entry.get("kind") == "query" and entry.get("parent_id"):
            eff_id = entry["parent_id"]
            text = entry.get("parent_text") or (by_id.get(eff_id) or {}).get("text", "")
            if not text:
                continue
        else:
            eff_id = entry["id"]
            text = entry["text"]
        score = round(scores[doc_id], 6)
        if eff_id in seen:
            if score > results[seen[eff_id]]["retrieval_score"]:
                results[seen[eff_id]]["retrieval_score"] = score
            continue
        seen[eff_id] = len(results)
        results.append({
            "id": eff_id,
            "text": text,
            "source": entry["source"],
            "retrieval_score": score,
        })
        if len(results) >= top_k:
            break

    return results
