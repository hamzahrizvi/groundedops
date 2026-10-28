import functools
import logging
import os

import numpy as np
from model_pins import revision

logger = logging.getLogger(__name__)

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
# Needs transformers >= 4.48, where ModernBERT is built in (5.14 installed).
# The model is loaded WITHOUT trust_remote_code (D-20261021-06): its config has
# no auto_map, so the flag never ran any code, and it would have let any model
# named in EMBED_MODEL run Python shipped with it. Pinned to a commit in
# model_pins.py.
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

_model = None   # a SentenceTransformer, or _OnnxEmbedder when EMBED_BACKEND=onnx


class _OnnxEmbedder:
    """SentenceTransformer stand-in on onnxruntime (EMBED_BACKEND=onnx, the
    default).

    Same model, same pinned commit: the publisher's fp32 export
    (onnx/model.onnx, 0.6 GB, fetched once into the HF cache). Measured
    2026-10-22 on the i7-1265U, real chunks, batches of 16, while the server's
    Confluence sync was embedding on the same CPU: 0.51-0.58 chunks/s against
    PyTorch's 0.12 (32 chunks) and 0.50 against 0.08 (96). OpenVINO on the
    same file was no faster (0.54). Vectors match the stored index (min
    cosine 0.9996; PyTorch re-embedding the same chunks gives 0.9991), so
    switching needs no re-embed; eval_retrieval.py on eval_cases_retrieval
    scores identically on both. The publisher's int8 file was 1.7x faster
    again but rejected: min cosine 0.916 would need a full re-embed and a
    retrieval re-measure.

    Only the encode() calls this module makes are supported. Pooling is the
    CLS token, which is what gte-modernbert's 1_Pooling config asks for; a
    model that pools otherwise is refused here and loads on PyTorch.
    """

    def __init__(self, name: str):
        import json
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from transformers import AutoTokenizer
        rev = revision(name)
        pooling = hf_hub_download(name, "1_Pooling/config.json", revision=rev)
        with open(pooling, encoding="utf-8") as f:
            if not json.load(f).get("pooling_mode_cls_token"):
                raise ValueError(f"{name} does not pool on the CLS token")
        path = hf_hub_download(name, "onnx/model.onnx", revision=rev)
        self.tokenizer = AutoTokenizer.from_pretrained(name, revision=rev)
        self.session = ort.InferenceSession(
            path, providers=["CPUExecutionProvider"])
        self._inputs = [i.name for i in self.session.get_inputs()]

    def encode(self, texts, convert_to_numpy=True, normalize_embeddings=True,
               batch_size=32):
        # Longest first, as SentenceTransformer does, so each batch pads to
        # a similar length; results go back in the caller's order.
        order = sorted(range(len(texts)), key=lambda i: -len(texts[i]))
        out = [None] * len(texts)
        for s in range(0, len(order), batch_size):
            idx = order[s:s + batch_size]
            enc = self.tokenizer([texts[i] for i in idx], padding=True,
                                 truncation=True, return_tensors="np")
            cls = self.session.run(
                None, {n: enc[n].astype(np.int64) for n in self._inputs})[0][:, 0]
            if normalize_embeddings:
                cls = cls / np.linalg.norm(cls, axis=1, keepdims=True)
            for i, v in zip(idx, cls):
                out[i] = v
        return np.array(out, dtype=np.float32)


def _get_model():
    global _model
    if _model is None:
        if os.getenv("EMBED_BACKEND", "onnx").strip().lower() == "onnx":
            try:
                logger.info("Loading embedding model on ONNX: %s", EMBED_MODEL)
                _model = _OnnxEmbedder(EMBED_MODEL)
            except Exception as exc:   # offline, failed fetch, bad file
                logger.warning("ONNX embedder unavailable (%s); "
                               "using PyTorch", exc)
        if _model is None:
            # Imported here, not at the top: sentence_transformers brings
            # torch and transformers with it (~8s), which nothing needs until
            # the first encode -- and every CLI tool and test imports this
            # module.
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(EMBED_MODEL, revision=revision(EMBED_MODEL))
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
    """The query vector, memoised on the exact text.

    One turn embeds the same resolved query three times -- FAQ ranking,
    retrieval and routing -- and the console's Pipeline check repeats a
    question verbatim. The model is a process-level constant (EMBED_MODEL),
    so a cached vector can never go stale. A copy is returned because a
    caller may normalise or slice in place."""
    return _embed_query_cached(_query_prefix() + query).copy()


@functools.lru_cache(maxsize=512)
def _embed_query_cached(text: str) -> np.ndarray:
    return _get_model().encode(
        [text],
        convert_to_numpy=True,
        normalize_embeddings=True
    )[0]
