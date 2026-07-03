import os
import logging
import tempfile

from parsing import extract_text
from chunking import chunk_text
from embeddings import embed_texts
from db import get_collection

logger = logging.getLogger(__name__)


def ingest_file(content: bytes, filename: str) -> int:
    """
    Parse, chunk, embed and store a file.

    Returns the number of chunks added (0 if duplicate or empty).
    """
    collection = get_collection()

    # ── Duplicate check ───────────────────────────────────────────────────────
    existing = collection.get(where={"source": filename})
    if existing and existing.get("ids"):
        logger.info(f"Skipping duplicate: {filename}")
        return 0

    # ── Save to temp file for parsing ────────────────────────────────────────
    # Use only the extension as suffix so extract_text() can detect the type
    suffix  = os.path.splitext(filename)[1]
    tmp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        # ── Extract ───────────────────────────────────────────────────────────
        text = extract_text(tmp_path)
        if not text or not text.strip():
            logger.warning(f"No text extracted from '{filename}'")
            return 0

        # ── Chunk ─────────────────────────────────────────────────────────────
        chunks = chunk_text(text)
        texts  = [c for c in chunks if c.strip()]
        if not texts:
            logger.warning(f"No usable chunks from '{filename}'")
            return 0

        # ── Embed ─────────────────────────────────────────────────────────────
        vectors = embed_texts(texts)

        # ── Store ─────────────────────────────────────────────────────────────
        ids = [f"{filename}_{i}" for i in range(len(texts))]

        collection.add(
            documents=texts,
            embeddings=[v.tolist() for v in vectors],
            metadatas=[{"source": filename} for _ in texts],
            ids=ids,
        )

        logger.info(f"Ingested '{filename}': {len(texts)} chunks")
        return len(texts)

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)