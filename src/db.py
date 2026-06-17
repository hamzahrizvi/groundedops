"""
Shared ChromaDB client. Both ingest.py and retrieval_db.py import from
here so they operate on the SAME persistent collection.
"""

import os
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


def get_collection() -> chromadb.Collection:
    global _collection
    if _collection is None:
        _collection = get_client().get_or_create_collection(COLLECTION_NAME)
    return _collection


def reset_collection() -> chromadb.Collection:
    global _collection
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