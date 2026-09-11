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
# v16.2 (2026-08-28): bge-small-en-v1.5 -> gte-modernbert-base. 384 -> 768
# dimensions, and a ModernBERT backbone rather than 2019-era BERT. The old
# model was the weakest link in a pipeline whose other stages measured well:
# on the query that exposed the RRF bug ("how much does the NV9S validator
# weigh"), gte scores the correct spec table at 0.867 cosine.
#
# Needs transformers >= 4.48 for ModernBERT (5.14 installed) and
# trust_remote_code, which is why that flag is set below rather than left
# to chance -- without it SentenceTransformer silently falls back and the
# load fails at a confusing place.
#
# CHANGING THIS REQUIRES A FULL RE-EMBED, and the dimension change means the
# old collection cannot be reused at all:  python reindex.py --from-store
EMBED_MODEL = os.getenv("EMBED_MODEL", "Alibaba-NLP/gte-modernbert-base")

# BGE is trained for asymmetric search: queries get an instruction prefix,
# passages do not. Omitting it costs a few points of recall. Empty for any
# non-BGE model, so EMBED_MODEL can be swapped back without code changes.
#
# GTE is trained WITHOUT such a prefix -- adding one measurably hurts it, so
# the "bge" test below is doing real work now rather than sitting dormant.
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_model = None


def _get_model():
    global _model
    if _model is None:
        # trust_remote_code is required by gte-modernbert and harmless for
        # models that ship no custom code.
        _model = SentenceTransformer(EMBED_MODEL, trust_remote_code=True)
    return _model


def _query_prefix() -> str:
    return _BGE_QUERY_PREFIX if "bge" in EMBED_MODEL.lower() else ""


def embedding_dim() -> int:
    """Dimension of the active model. Used by reindex to detect that an
    existing collection was built with a different model and must be
    rebuilt rather than appended to."""
    m = _get_model()
    for attr in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
        fn = getattr(m, attr, None)
        if callable(fn):
            return int(fn())
    return int(m.encode(["x"], convert_to_numpy=True).shape[1])


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
