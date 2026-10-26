"""Commit hashes of every Hugging Face model the backend may load (D-20261021-06).

A model loaded by name alone follows its repository's main branch, so a
changed or hijacked upstream repo would change what runs here. Every load in
embeddings.py, reranker.py and grounding.py passes revision=revision(name),
and docker/Dockerfile.backend bakes these exact revisions and runs offline.
Moving to a newer model version means editing its hash here (for the
embedder, also a re-index). No model is loaded with trust_remote_code.
"""
import logging

logger = logging.getLogger(__name__)

REVISIONS = {
    # embedder (embeddings.EMBED_MODEL)
    "Alibaba-NLP/gte-modernbert-base": "e7f32e3c00f91d699e8c43b53106206bcc72bb22",
    # reranker profiles "fast" and "accurate" (reranker.PROFILES)
    "cross-encoder/ms-marco-MiniLM-L-6-v2": "233902d25c440f23af6f7d6e94d2946bac0bee0a",
    "BAAI/bge-reranker-base": "2cfc18c9415c912f9d8155881c133215df768a70",
    # answer checker, PyTorch and ONNX (grounding.NLI_MODEL_NAME)
    "cross-encoder/nli-deberta-v3-small": "fa2804872c3b4bd748f38c0185cc85775361e735",
}


def revision(name: str) -> str | None:
    """The pinned commit for `name`, or None (logged) for a model named only
    by the environment and not listed above."""
    rev = REVISIONS.get(name)
    if rev is None:
        logger.warning("Model %s has no pinned revision in model_pins.py; "
                       "loading whatever its repository serves now", name)
    return rev
