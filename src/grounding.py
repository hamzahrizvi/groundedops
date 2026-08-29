"""
Grounding checker using a small NLI cross-encoder.

Splits the generated answer into checkable units (see text_utils.split_units)
and verifies each is entailed by at least one retrieved context chunk.
Returns the minimum entailment score across all units — so a single
unsupported claim will pull the score below threshold.

Model used: cross-encoder/nli-deberta-v3-small (~180 MB, CPU-ok)

Labels, read from the loaded model's own config rather than trusted from
here:  0 = contradiction | 1 = entailment | 2 = neutral

This line previously read "1 = neutral | 2 = entailment", which is where the
2026-08-28 bug came from — the code faithfully implemented the comment, and
the comment was wrong. See _entailment_index below; the column is now
resolved by label NAME so a wrong note here cannot cause it again.
"""

import logging
import re

from sentence_transformers import CrossEncoder

from text_utils import split_units

logger = logging.getLogger(__name__)

NLI_MODEL_NAME = "cross-encoder/nli-deberta-v3-small"
_nli_model: CrossEncoder | None = None

# This model's head is {0: contradiction, 1: entailment, 2: neutral}.
#
# 2026-08-28: every score in this file was read from column 2 and called
# "entailment". Column 2 is NEUTRAL. The gate was therefore measuring "is
# this claim UNRELATED to the context" and passing anything that scored
# high -- which is close to the exact opposite of what a grounding gate is
# for. Measured on a four-fact context:
#
#   "The unit has a built-in thermal printer"   0.9936  (invented -> PASSED)
#   "The NV9USB+ includes facial recognition"   0.9928  (invented -> PASSED)
#   "The device is manufactured in Belgium"     0.9933  (invented -> PASSED)
#   "Supply voltage is 12V DC nominal"          0.1726  (VERBATIM TRUE -> refused)
#
# Only outright contradictions ("500 notes" when the context says 30) scored
# low, and those score low on neutral for the same reason they score low on
# entailment -- which is why the gate looked like it worked.
#
# Resolved by name rather than hardcoded: a future model swap with a
# different head order would silently reintroduce this, and it is invisible
# in every metric (a broken gate reports high confidence).
_ENTAIL_IDX: int | None = None


def _entailment_index() -> int:
    """Column of the entailment logit for the loaded NLI model."""
    global _ENTAIL_IDX
    if _ENTAIL_IDX is None:
        cfg = (getattr(_get_nli_model(), "config", None)
               or getattr(getattr(_get_nli_model(), "model", None), "config", None))
        id2label = dict(getattr(cfg, "id2label", {}) or {})
        found = next((int(i) for i, lab in id2label.items()
                      if str(lab).lower().startswith("entail")), None)
        if found is None:
            logger.error("NLI model exposes no 'entailment' label (%s); "
                         "falling back to column 1", id2label)
            found = 1
        _ENTAIL_IDX = found
        logger.info("NLI entailment column = %d (%s)", found, id2label)
    return _ENTAIL_IDX


def _get_nli_model() -> CrossEncoder:
    global _nli_model
    if _nli_model is None:
        logger.info(f"Loading NLI model: {NLI_MODEL_NAME}")
        _nli_model = CrossEncoder(NLI_MODEL_NAME)
    return _nli_model


# The second half of the 2026-08-28 fix. This model is trained on SNLI/MNLI,
# where the premise is ONE sentence. Handing it a whole retrieved chunk
# (~1200 chars, a dozen sentences) is far outside that distribution and it
# collapses -- measured on a four-sentence premise:
#
#                                        whole chunk   best sentence
#   "The NV11+ Note Float holds 30 notes"     0.0037          0.9877
#   "Supply voltage is 12V DC nominal"        0.0004          0.9828
#   "The unit has a built-in thermal printer" 0.0001          0.0003
#
# Whole-chunk premises score TRUE claims at ~0.00, which is why fixing only
# the column would have turned the gate into "refuse everything". Scoring
# each context SENTENCE separately and taking the best restores a clean
# margin: true claims 0.97-0.99, invented ones <0.002.
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_MIN_PREMISE = 15


def _premises(context_texts: list[str]) -> list[str]:
    """Context split into sentence-sized premises, deduped.

    Very short fragments are dropped: a table row like "NV22: 2.92 Kg" is
    kept (it is a real premise) but a stray "-" or a page number is noise
    that only costs a model call.
    """
    seen, out = set(), []
    for text in context_texts:
        for s in _SENT_SPLIT.split(text or ""):
            s = s.strip()
            if len(s) >= _MIN_PREMISE and s not in seen:
                seen.add(s)
                out.append(s)
    return out


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

        premises = _premises(context_texts)
        if not premises:
            return False, 0.0

        col = _entailment_index()
        min_score = 1.0

        for unit in units:
            logits = model.predict([(p, unit) for p in premises],
                                   apply_softmax=True)
            best_entailment = float(max(logits[:, col]))
            min_score = min(min_score, best_entailment)

        return min_score >= threshold, round(min_score, 4)

    except Exception as exc:
        logger.error(f"Grounding check failed: {exc}")
        return True, 1.0   # fail open


def score_unit(unit: str, context_chunks: list[dict]) -> float:
    """Entailment score for ONE unit against the context.

    Extracted so the streaming path can verify each sentence as it lands
    instead of waiting for the whole answer. check_grounding above is the
    same computation over every unit, taking the minimum -- so a sentence
    scored here and a sentence scored there get identical treatment, and
    the streamed and non-streamed paths cannot drift apart in strictness.

    Fails OPEN (1.0) on model error, matching check_grounding: an NLI crash
    must never silently turn into "this answer is a hallucination".
    """
    try:
        if not unit or not unit.strip():
            return 1.0
        context_texts = [c["text"] if isinstance(c, dict) else str(c)
                         for c in context_chunks]
        if not context_texts:
            return 0.0
        premises = _premises(context_texts)
        if not premises:
            return 0.0
        model = _get_nli_model()
        logits = model.predict([(p, unit) for p in premises],
                               apply_softmax=True)
        return float(max(logits[:, _entailment_index()]))
    except Exception as exc:
        logger.error(f"score_unit failed: {exc}")
        return 1.0


class StreamGrounder:
    """Verifies a streamed answer sentence by sentence.

    Feed it deltas; it buffers until a sentence boundary, scores the
    completed sentence, and reports whether it is safe to release. The
    first sentence that fails stops the stream -- the caller replaces the
    whole answer with the refusal rather than leaving a half-answer on
    screen, because a truncated answer reads as a complete one.

    Deliberately releases text only in whole verified sentences. Releasing
    token-by-token and retracting afterwards would put unverified claims in
    front of a customer, which is the exact property this system exists to
    guarantee against.
    """

    def __init__(self, context_chunks, threshold):
        self.context = context_chunks
        self.threshold = threshold
        self._buf = ""
        self.released = ""
        self.min_score = 1.0
        self.failed_unit = None

    def _boundary(self, text: str) -> int:
        """Index just past the last sentence terminator, or -1."""
        best = -1
        for i, ch in enumerate(text):
            if ch in ".!?\n":
                nxt = text[i + 1] if i + 1 < len(text) else " "
                if ch == "\n" or nxt.isspace() or i + 1 == len(text):
                    best = i + 1
        return best

    def feed(self, delta: str):
        """Absorb a delta. Returns (text_to_release, ok).

        ok=False means this sentence is not supported by the context and the
        caller must stop and refuse.
        """
        self._buf += delta
        cut = self._boundary(self._buf)
        if cut <= 0:
            return "", True
        candidate, self._buf = self._buf[:cut], self._buf[cut:]
        return self._verify(candidate)

    def finish(self):
        """Verify whatever is left in the buffer at end of stream."""
        if not self._buf.strip():
            return "", True
        candidate, self._buf = self._buf, ""
        return self._verify(candidate)

    def _verify(self, candidate: str):
        # split_units is what check_grounding uses, so a fragment it does not
        # consider checkable (too short, a bare list marker) is released
        # unscored here exactly as it would be ignored there.
        #
        # Uses the module-level import deliberately. A lazy `from text_utils
        # import split_units` here resolves at CALL time, which under the test
        # harness (it stubs text_utils in sys.modules) picked up a stub with no
        # split_units and failed only in tests -- the classic import that works
        # everywhere except where it is checked.
        units = split_units(candidate)
        for u in units:
            s = score_unit(u, self.context)
            self.min_score = min(self.min_score, s)
            if s < self.threshold:
                self.failed_unit = u
                return "", False
        self.released += candidate
        return candidate, True
