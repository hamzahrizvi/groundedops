"""
Shared ChromaDB client. Both ingest.py and retrieval_db.py import from
here so they operate on the SAME persistent collection.
"""

import os
import time
import logging
import chromadb
from typing import Optional

from docindex import product_keys

logger = logging.getLogger(__name__)

CHROMA_DIR = os.getenv("CHROMA_DIR", "./chroma_db")
COLLECTION_NAME = "docs"

_client: Optional[chromadb.PersistentClient] = None
_collection: Optional[chromadb.Collection] = None


def get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        os.makedirs(CHROMA_DIR, exist_ok=True)
        _client = chromadb.PersistentClient(path=CHROMA_DIR)
        logger.info(f"ChromaDB client created at '{CHROMA_DIR}'")
    return _client


_CHECK_SECS = float(os.getenv("CHROMA_HANDLE_CHECK_SECS", "5"))
_checked_at = 0.0


def invalidate_retrieval_cache() -> None:
    """Invalidate derived in-process search state after any index mutation."""
    import docindex
    docindex.invalidate()
    try:
        from retrieval_db import _invalidate_bm25_cache
        _invalidate_bm25_cache()
    except Exception as exc:
        # During startup retrieval_db may still be importing db. Count-based
        # invalidation remains the fallback for inserts/deletes in that case.
        logger.debug("retrieval cache invalidation deferred: %s", exc)


def get_collection() -> chromadb.Collection:
    """The shared 'docs' collection, re-acquired if it was reset elsewhere.

    A Collection handle is bound to the collection's UUID at the moment it is
    fetched, and reset_collection() below deletes and recreates the row -- so
    the new collection has a NEW UUID and every handle taken before it points
    at a row that no longer exists.

    Chroma does not raise for that. Reads against the dead UUID return EMPTY.
    On 2026-09-01 a server that had been up since 08-30 therefore reported
    zero documents and zero chunks -- admin inventory, catalogue doc_counts
    and retrieval all silently reading as an empty corpus -- while the store
    on disk held all 11 documents and 684 chunks the whole time. A reindex run
    from any second process (a CLI re-ingest, a test run, the scheduled
    backup) is enough to cause it, and it looks exactly like data loss.

    So the handle is re-validated against the client's current UUID for the
    name, at most once every CHROMA_HANDLE_CHECK_SECS.
    """
    global _collection, _checked_at
    now = time.monotonic()
    if _collection is not None and now - _checked_at < _CHECK_SECS:
        return _collection

    client = get_client()
    if _collection is not None:
        try:
            live = client.get_collection(COLLECTION_NAME)
            if live.id != _collection.id:
                logger.warning(
                    f"collection '{COLLECTION_NAME}' was reset by another "
                    f"process ({_collection.id} -> {live.id}); "
                    "re-acquiring the handle")
                _collection = live
        except Exception as exc:
            # Gone entirely, mid-reset, or the store is being rebuilt.
            logger.warning(f"re-acquiring collection handle: {exc}")
            _collection = None

    if _collection is None:
        _collection = client.get_or_create_collection(COLLECTION_NAME)
    _checked_at = now
    return _collection


def reset_collection() -> chromadb.Collection:
    global _collection, _checked_at
    _checked_at = time.monotonic()
    client = get_client()
    try:
        client.delete_collection(COLLECTION_NAME)
        logger.info("Collection deleted")
    except Exception:
        pass
    _collection = client.create_collection(COLLECTION_NAME)
    invalidate_retrieval_cache()
    logger.info("Collection recreated")
    return _collection


def get_stats() -> dict:
    from docindex import source_index
    count = get_collection().count()
    try:
        sources = sorted(row["source"] for row in source_index())
    except Exception:
        sources = []
    return {"total_chunks": count, "sources": sources}


def delete_source(source: str) -> int:
    col = get_collection()
    result = col.get(where={"source": source})
    ids = result.get("ids", []) if result else []
    if ids:
        col.delete(ids=ids)
        invalidate_retrieval_cache()
        logger.info(f"Deleted source '{source}' ({len(ids)} chunks)")
    return len(ids)


def stale_product_flags(meta: dict, keys: list[str] | None = None) -> list[str]:
    """The prod_* flags on a chunk that its product tags no longer name.

    `keys` defaults to the chunk's own product tags."""
    keep = set(product_keys(meta) if keys is None else keys)
    return [k for k in meta if k.startswith("prod_") and k[5:] not in keep]


def with_product_tags(meta: dict, keys: list[str]) -> dict:
    """A copy of `meta` for col.update() that tags the chunk to exactly `keys`.

    Chroma's update MERGES metadata: a key missing from the dict is KEPT, and
    only a key set to None is deleted. Popping a stale prod_* flag before
    update() therefore left it in the index, which is how 440 chunks moved
    off biometrics_general still carried prod_biometrics_general=True -- and
    the dense arm's shared-documents clause matches on that flag, so NV9
    Spectral rows reached MyCheckr-scoped answers. So stale flags are written
    as None. The singular "product" is rewritten rather than dropped: the
    dense arm matches on it and reindex.py/clarify.py still read it, and a
    pop() here never reached the index either, so it kept the OLD key.
    """
    out = dict(meta)
    for k in stale_product_flags(meta, keys):
        out[k] = None
    out["products"] = ",".join(keys)
    out["product"] = ",".join(keys)
    for k in keys:
        out["prod_" + k] = True
    return out


def count_by_product(product_key: str) -> int:
    """How many chunks are tagged to this product. Used to tell an operator
    what a deletion is about to affect BEFORE they confirm it."""
    col = get_collection()
    got = col.get(include=["metadatas"])
    return sum(1 for m in (got.get("metadatas") or [])
               if product_key in product_keys(m))


def retag_product(old_key: str, new_key: str | None) -> int:
    """Move every chunk tagged `old_key` to `new_key`, or drop the tag when
    `new_key` is None.

    Returns the number of chunks changed. Writes both spellings and the
    prod_* flags through with_product_tags, so the dropped key's flag is
    really deleted from the index.

    A chunk tagged to several products keeps its other tags — retagging one
    product must not strip a document's membership of another.
    """
    if not old_key or old_key == new_key:
        return 0
    col = get_collection()
    got = col.get(include=["metadatas"])
    ids = got.get("ids") or []
    metas = got.get("metadatas") or []

    change_ids, change_metas = [], []
    for cid, meta in zip(ids, metas):
        keys = product_keys(meta)
        if old_key not in keys:
            continue
        keys = [k for k in keys if k != old_key]
        if new_key and new_key not in keys:
            keys.append(new_key)
        change_ids.append(cid)
        change_metas.append(with_product_tags(meta, keys))

    if change_ids:
        col.update(ids=change_ids, metadatas=change_metas)
        invalidate_retrieval_cache()
        logger.info(f"Retagged {len(change_ids)} chunk(s): "
                    f"{old_key!r} -> {new_key!r}")
    return len(change_ids)


def delete_by_product(product_key: str) -> int:
    """Delete every chunk whose ONLY product tag is this one.

    A chunk shared with another product is retagged instead of deleted --
    deleting a shared document because one of its products went away would
    take content away from a product nobody touched.
    """
    if not product_key:
        return 0
    col = get_collection()
    got = col.get(include=["metadatas"])
    ids = got.get("ids") or []
    metas = got.get("metadatas") or []

    doomed, shared_ids, shared_metas = [], [], []
    for cid, meta in zip(ids, metas):
        keys = product_keys(meta)
        if product_key not in keys:
            continue
        remaining = [k for k in keys if k != product_key]
        if remaining:
            shared_ids.append(cid)
            shared_metas.append(with_product_tags(meta, remaining))
        else:
            doomed.append(cid)

    if shared_ids:
        col.update(ids=shared_ids, metadatas=shared_metas)
    if doomed:
        col.delete(ids=doomed)
    if shared_ids or doomed:
        invalidate_retrieval_cache()
    logger.info(f"delete_by_product {product_key!r}: removed {len(doomed)} "
                f"chunk(s), kept {len(shared_ids)} shared with other products")
    return len(doomed)


def get_chunks_by_ids(ids: list[str]) -> list[dict]:
    """Fetch full chunk text/source for a list of chunk ids — backs the
    'clickable source' feature (show the actual retrieved content)."""
    if not ids:
        return []
    col = get_collection()
    result = col.get(ids=ids, include=["documents", "metadatas"])
    chunks = []
    for doc_id, doc, meta in zip(result["ids"], result["documents"], result["metadatas"]):
        chunks.append({
            "id": doc_id,
            "text": doc,
            "source": meta.get("source", "unknown"),
        })
    return chunks
