"""
Hybrid retrieval: BM25 (full corpus) + dense (ChromaDB) merged via RRF.

Each ranking is computed INDEPENDENTLY over the full corpus, so a chunk
that's a strong keyword match but a weak embedding match (or vice versa)
can still surface — a chunk ranking #1 on BM25 but absent from dense
results entirely is not invisible to the merge.

CAVEAT, and it is a big one (2026-08-28): "not invisible" is not the same
as "competitive". RRF rewards agreement, so a chunk only ONE ranking finds
scores at most 1/(RRF_K+1) and routinely loses to chunks both rankings rank
mediocrely. Surfacing it therefore depends on the candidate window being
wide enough to carry it to the reranker — see RETRIEVAL_CANDIDATE_MARGIN.
For years the window was exactly top_k and this docstring's promise did not
hold in practice.
"""

import os
import threading
from rank_bm25 import BM25Okapi

from embeddings import embed_query
from db import get_collection
from text_utils import rrf_merge

RRF_K = 60

# How many candidates reach the reranker, as a multiple of top_k. 1.0 is the
# pre-2026-08-28 behaviour (reranker saw only top_k); 2.0 hands it the full
# margin retrieve_from_db already computes. See the note at the cut itself.
# DEFAULT 1.0 ON EVIDENCE, not on principle. Widening the window to 2.0 was
# the obvious fix for the single-list problem below and it MEASURED BADLY:
# over the 34-case suite forced through retrieval it lost two cases, gained
# none, and dropped mean grounding 0.9934 -> 0.9539. Sixteen extra mediocre
# candidates displace good ones when the reranker picks CONTEXT_K. Kept as a
# knob so the experiment is repeatable, but do not raise it without
# re-running that comparison.
RETRIEVAL_CANDIDATE_MARGIN = float(os.getenv("RETRIEVAL_CANDIDATE_MARGIN", "1.0"))

# Guarantee the top N of EACH individual ranking reaches the reranker, even
# when RRF buried them for being found by only one retriever. 0 disables.
#
# This is the fix that measured well, because it is surgical. Across the 51
# questions in both suites it rescued a candidate on 12 of them, at an
# average of 0.45 extra candidates per query, and changed the final answer
# on exactly ONE -- the query that was broken (rerank top 0.216 -> 0.993).
# End to end: no regressions on either suite, grounding and latency flat.
RETRIEVAL_ARM_GUARANTEE = int(os.getenv("RETRIEVAL_ARM_GUARANTEE", "3"))

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
                # v12.0: carried through so answers can cite a page.
                "page": m.get("page"),
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
    #
    # 2026-08-28: the margin built here was thrown away again by the
    # `len(results) >= top_k` break below, so the reranker only ever saw
    # top_k. That is not academic. RRF rewards agreement between the two
    # rankings: with RRF_K=60 a chunk found by ONE retriever caps at
    # 1/61 = 0.0164, while anything both rank in their top ten scores
    # ~0.028+. So a chunk ranked #1 by dense and missed entirely by BM25
    # loses to chunks both rank ~7th -- and gets cut before the reranker,
    # which is the one stage that would have recognised it.
    #
    # Measured on "How much does the NV9S validator weigh on its own?":
    # the correct chunk (p25, "Validator NV9S: 1.05 Kg") was dense #1,
    # BM25 absent, RRF rank 20 of 46 -- four places outside a 16 cut. Given
    # to the reranker it scores 0.9928; the table-of-contents chunk that
    # won instead scores 0.2165. This hits spec tables hardest, where dense
    # understands weigh->Weights and BM25 sees only numbers.
    #
    # RETRIEVAL_CANDIDATE_MARGIN is the multiple of top_k handed to the
    # reranker. 1 is the old behaviour, kept so the change can be A/B'd
    # and reverted by config rather than by a deploy.
    ranked_ids = sorted(scores, key=scores.get, reverse=True)[:top_k * 2]
    keep_n = max(top_k, int(round(top_k * RETRIEVAL_CANDIDATE_MARGIN)))

    # Measured 2026-08-28: simply widening keep_n to 2x fixes the single-list
    # case but costs two others and drops mean grounding 0.993 -> 0.954,
    # because 16 extra mediocre candidates displace good ones when the
    # reranker picks its CONTEXT_K. The targeted alternative: keep the RRF
    # order as-is, but guarantee the top ARM_GUARANTEE entries from EACH
    # ranking survive even if RRF buried them. That admits ~2-6 extra
    # candidates rather than 16, which is the whole point -- it rescues the
    # "one retriever is certain, the other has never heard of it" case
    # without diluting the consensus ones. 0 disables.
    if RETRIEVAL_ARM_GUARANTEE > 0:
        head = ranked_ids[:keep_n]
        seen = set(head)
        rescued = []
        for arm in (dense_ids, bm25_ids):
            for cid in arm[:RETRIEVAL_ARM_GUARANTEE]:
                if cid not in seen:
                    seen.add(cid)
                    rescued.append(cid)
        ranked_ids = head + rescued
        keep_n = len(ranked_ids)

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
            "page": entry.get("page"),
            # v15.2: product/category were collected into the chunk cache above
            # but dropped here, so every result reached the caller with
            # product=None. Scoped retrieval still worked (that filters inside
            # Chroma on the stored metadata), which is why this went unnoticed
            # -- but anything reasoning about the products a result set SPANS
            # saw nothing. Concretely: the unscoped product-disambiguation
            # check in main.py computed an empty span and never fired, so
            # "give pinout for nv9?" with no product silently blended the
            # NV9 Spectral and NV9USB+ manuals into one answer about pin 13
            # instead of asking which product was meant.
            "product": entry.get("product", ""),
            "category": entry.get("category", ""),
            "retrieval_score": round(scores[doc_id], 6),
        })
        if len(results) >= keep_n:
            break

    return results
