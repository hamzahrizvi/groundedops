import os

from sentence_transformers import SentenceTransformer
import numpy as np

# v15: was all-MiniLM-L6-v2, whose 256-token limit was the binding constraint
# on chunk size. Measured, not assumed: when chunks grew 500 -> 1200 chars,
# first-pass recall@1 FELL (0.500 -> 0.438) while post-rerank recall rose,
# because the bi-encoder was truncating what the 512-token cross-encoder could
# still read. bge-small-en-v1.5 doubles the window to 512 tokens and keeps the
# same 384 dimensions, so the store shape is unchanged.
#
# CHANGING THIS REQUIRES A FULL RE-EMBED: python reindex.py --from-store
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")

# BGE is trained for asymmetric search: queries get an instruction prefix,
# passages do not. Omitting it costs a few points of recall. Empty for any
# non-BGE model, so EMBED_MODEL can be swapped back without code changes.
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_model = None


def _get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBED_MODEL)
    return _model


def _query_prefix() -> str:
    return _BGE_QUERY_PREFIX if "bge" in EMBED_MODEL.lower() else ""


def embed_texts(texts: list[str]) -> np.ndarray:
    return _get_model().encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True
    )


def embed_query(query: str) -> np.ndarray:
    return _get_model().encode(
        [_query_prefix() + query],
        convert_to_numpy=True,
        normalize_embeddings=True
    )[0]
