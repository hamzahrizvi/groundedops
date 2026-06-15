"""
Shared ChromaDB client.

Both ingest.py and retrieval_db.py import from here so they
operate on the SAME collection — previously each created its own
in-memory chromadb.Client(), which meant ingested data was invisible
to the retrieval layer.

Uses PersistentClient so documents survive server restarts.
Set CHROMA_DIR env var to override storage location.
"""

import os
import logging
import chromadb
from typing import Optional

logger = logging.getLogger(__name__)

CHROMA_DIR      = os.getenv("CHROMA_DIR", "./chroma_db")
COLLECTION_NAME = "docs"

_client:     Optional[chromadb.PersistentClient] = None 
_collection: Optional[chromadb.Collection]       = None

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
    """Delete and recreate the collection."""
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
    """Return chunk count and unique source filenames."""
    col = get_collection()
    count = col.count()
    try:
        result  = col.get(include=["metadatas"])
        sources = sorted({
            m.get("source", "unknown")
            for m in result["metadatas"]
            if m.get("source")
        })
    except Exception:
        sources = []
    return {"total_chunks": count, "sources": sources}