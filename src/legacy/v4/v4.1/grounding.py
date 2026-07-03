"""
Grounding checker using a small NLI cross-encoder.

Splits the generated answer into checkable units (see text_utils.split_units)
and verifies each is entailed by at least one retrieved context chunk.
Returns the minimum entailment score across all units — so a single
unsupported claim will pull the score below threshold.

Model used: cross-encoder/nli-deberta-v3-small (~180 MB, CPU-ok)
Labels:  0 = contradiction  |  1 = neutral  |  2 = entailment
"""

import logging
from sentence_transformers import CrossEncoder

from text_utils import split_units

logger = logging.getLogger(__name__)

NLI_MODEL_NAME = "cross-encoder/nli-deberta-v3-small"
_nli_model: CrossEncoder | None = None


def _get_nli_model() -> CrossEncoder:
    global _nli_model
    if _nli_model is None:
        logger.info(f"Loading NLI model: {NLI_MODEL_NAME}")
        _nli_model = CrossEncoder(NLI_MODEL_NAME)
    return _nli_model


def check_grounding(
    answer: str,
    context_chunks: list[dict],
    threshold: float = 0.35,
) -> tuple[bool, float]:
    """
    Returns (is_grounded, min_entailment_score).

    is_grounded=False means at least one unit in the answer
    is not supported by the retrieved context.
    Fails open (returns True, 1.0) if the NLI model itself crashes,
    so a model error never hard-blocks a response.
    """
    try:
        model = _get_nli_model()

        units = split_units(answer)

        if not units:
            return True, 1.0

        context_texts = [
            c["text"] if isinstance(c, dict) else str(c)
            for c in context_chunks
        ]

        if not context_texts:
            return False, 0.0

        min_score = 1.0

        for unit in units:
            pairs = [(ctx, unit) for ctx in context_texts]
            logits = model.predict(pairs, apply_softmax=True)
            best_entailment = float(max(logits[:, 2]))
            min_score = min(min_score, best_entailment)

        return min_score >= threshold, round(min_score, 4)

    except Exception as exc:
        logger.error(f"Grounding check failed: {exc}")
        return True, 1.0   # fail open