from sentence_transformers import SentenceTransformer
import numpy as np

_model = None


def _get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def embed_texts(texts: list[str]) -> np.ndarray:
    return _get_model().encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True
    )


def embed_query(query: str) -> np.ndarray:
    return _get_model().encode(
        [query],
        convert_to_numpy=True,
        normalize_embeddings=True
    )[0]