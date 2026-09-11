"""
Shared ChromaDB client. Both ingest.py and retrieval_db.py import from
here so they operate on the SAME persistent collection.
"""

import os
import time
import logging
import chromadb
from typing import Optional

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
    logger.info("Collection recreated")
    return _collection


def get_stats() -> dict:
    col = get_collection()
    count = col.count()
    try:
        result = col.get(include=["metadatas"])
        sources = sorted({
            m.get("source", "unknown")
            for m in result["metadatas"]
            if m.get("source")
        })
    except Exception:
        sources = []
    return {"total_chunks": count, "sources": sources}


def delete_source(source: str) -> int:
    col = get_collection()
    result = col.get(where={"source": source})
    ids = result.get("ids", []) if result else []
    if ids:
        col.delete(ids=ids)
        logger.info(f"Deleted source '{source}' ({len(ids)} chunks)")
    return len(ids)


def _product_keys(meta: dict) -> list[str]:
    """The product tags on a chunk.

    Reads BOTH "products" and "product": ingest.py writes the plural and
    older paths wrote the singular, so anything that only checks one key
    silently misses half the corpus. That mismatch has bitten this project
    before (see PROJECT_MAP's note on the product-metadata key).
    """
    raw = meta.get("products") or meta.get("product") or ""
    return [k.strip() for k in str(raw).split(",") if k.strip()]


def count_by_product(product_key: str) -> int:
    """How many chunks are tagged to this product. Used to tell an operator
    what a deletion is about to affect BEFORE they confirm it."""
    col = get_collection()
    got = col.get(include=["metadatas"])
    return sum(1 for m in (got.get("metadatas") or [])
               if product_key in _product_keys(m))


def retag_product(old_key: str, new_key: str | None) -> int:
    """Move every chunk tagged `old_key` to `new_key`, or drop the tag when
    `new_key` is None.

    Returns the number of chunks changed. Writes both metadata keys back in
    the plural form so the corpus converges on one spelling as things are
    retagged, rather than accumulating more of the split above.

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
        keys = _product_keys(meta)
        if old_key not in keys:
            continue
        keys = [k for k in keys if k != old_key]
        if new_key and new_key not in keys:
            keys.append(new_key)
        updated = dict(meta)
        updated["products"] = ",".join(keys)
        updated.pop("product", None)      # collapse onto the plural spelling
        change_ids.append(cid)
        change_metas.append(updated)

    if change_ids:
        col.update(ids=change_ids, metadatas=change_metas)
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
        keys = _product_keys(meta)
        if product_key not in keys:
            continue
        remaining = [k for k in keys if k != product_key]
        if remaining:
            updated = dict(meta)
            updated["products"] = ",".join(remaining)
            updated.pop("product", None)
            shared_ids.append(cid)
            shared_metas.append(updated)
        else:
            doomed.append(cid)

    if shared_ids:
        col.update(ids=shared_ids, metadatas=shared_metas)
    if doomed:
        col.delete(ids=doomed)
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
