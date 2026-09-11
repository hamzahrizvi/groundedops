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
import os
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

# Splitting on EVERY newline cut sentences in half, because PDF text wraps
# mid-sentence. The manual's own definition of MyCheckr arrived as two
# premises -- "...performs anonymous age estima" and "age-restricted goods."
# -- and neither half entails the answer built from the whole (0.0029 and
# 0.0008; rejoined, 0.9763). That is why every "what is X?" answer was being
# refused while spec lookups passed.
#
# A line is a continuation when it does not end the sentence and the next
# line does not begin one. Table rows are left alone: they are newline-
# separated by nature and _table_row_sentence depends on that.
_CONT = re.compile(r"([^.!?:;|\n])\n(?![ \t]*[-*•|])(?=[a-z(])")


def _dewrap(text: str) -> str:
    """Rejoin lines a PDF wrapped mid-sentence, leaving real breaks alone."""
    prev = None
    out = text or ""
    while out != prev:                 # a sentence can wrap several times
        prev = out
        out = _CONT.sub(r"\1 ", out)
    return out


def _table_row_sentence(header: str, row: str) -> str | None:
    """Turn a pipe table row into a sentence, using its header for column names.

    Splitting a table into lines strands each row from the header that says
    what its columns MEAN. The NLI model was trained on English sentences,
    not pipe syntax, so a stranded row is close to unreadable to it:

        Environment | Minimum | Maximum          -> 0.0008
        Temperature | +5°C / 37.4°F | +50°C ...  -> 0.0519   (the real answer)

    Rejoining them as "Temperature: Minimum +5°C / 37.4°F, Maximum +50°C /
    122°F" gives it something it can actually judge. Returns None when the
    shapes do not line up, in which case the caller keeps the raw row.
    """
    hcells = [c.strip() for c in header.split("|")]
    rcells = [c.strip() for c in row.split("|")]
    if len(hcells) < 2 or len(rcells) < 2 or len(rcells) > len(hcells):
        return None
    label = rcells[0]
    if not label:
        return None
    parts = [f"{hcells[i]} {rcells[i]}".strip()
             for i in range(1, len(rcells)) if rcells[i]]
    if not parts:
        return None
    return f"{label}: " + ", ".join(parts)


def _premises(context_texts: list[str]) -> list[str]:
    """Context split into sentence-sized premises, deduped.

    Very short fragments are dropped: a table row like "NV22: 2.92 Kg" is
    kept (it is a real premise) but a stray "-" or a page number is noise
    that only costs a model call.

    Pipe table rows are emitted BOTH raw and header-rejoined (see
    _table_row_sentence). Both, not just the rejoined form, because scoring
    takes the max over premises: the raw row is sometimes self-explanatory,
    the rejoined one carries the column meaning, and whichever reads better
    to the model wins. The cost is a few extra pairs through a small model.
    """
    seen, out = set(), []

    def add(s):
        s = (s or "").strip()
        if len(s) >= _MIN_PREMISE and s not in seen:
            seen.add(s)
            out.append(s)

    for text in context_texts:
        header = None
        for s in _SENT_SPLIT.split(_dewrap(text or "")):
            s = s.strip()
            if not s:
                continue
            if "|" in s:
                # First pipe line of a run is the header; the rest are rows.
                if header is None:
                    header = s
                else:
                    add(_table_row_sentence(header, s))
            else:
                header = None      # a prose line ends the table
            add(s)
    return out


_STOPish = {"the", "a", "an", "is", "are", "of", "for", "to", "and", "or",
            "in", "on", "at", "it", "its", "this", "that", "with", "as",
            "be", "by", "from", "was", "were", "has", "have", "can", "will"}
MAX_PREMISES = int(os.getenv("GROUNDING_MAX_PREMISES", "12"))


def _relevant_premises(unit: str, premises: list[str]) -> list[str]:
    """The premises worth scoring this claim against.

    A premise sharing no content word with the claim cannot entail it, so
    running the cross-encoder over all of them is work with a known answer.
    A retrieved context yields ~86 premises and the NLI pass was costing
    1.2-3.6s of a 6.4s query -- the single largest unattributed slice.

    Ranked by shared content words, capped at MAX_PREMISES. Falls back to the
    first MAX_PREMISES when nothing overlaps, so a claim phrased entirely in
    synonyms is still scored rather than silently refused.
    """
    words = {w for w in re.findall(r"[a-z0-9]+", (unit or "").lower())
             if len(w) > 2 and w not in _STOPish}
    if not words:
        return premises[:MAX_PREMISES]
    scored = []
    for p in premises:
        pw = set(re.findall(r"[a-z0-9]+", p.lower()))
        overlap = len(words & pw)
        if overlap:
            scored.append((overlap, p))
    if not scored:
        return premises[:MAX_PREMISES]
    scored.sort(key=lambda t: -t[0])
    return [p for _, p in scored[:MAX_PREMISES]]


# A generated answer often packs several facts into ONE sentence ("MyCheckr
# is an all-in-one device that performs anonymous age estimation, requires no
# integration and offers world-leading accuracy"). No single source sentence
# entails all of it, so sentence-only premises scored those at ~0.001 and the
# gate refused every descriptive answer -- which is why "what is X and what
# does it do?", the first thing a new customer asks, was being declined while
# spec lookups passed.
#
# Measured on that exact sentence:
#     best single premise      0.0010
#     combined top-2           0.0049
#     combined top-3           0.0355
#     combined top-4           0.9947
#     combined top-6           0.5882
#
# So a WINDOW is scored alongside the individual premises and the best of all
# of them wins. Six is worse than four: past a few sentences the premise
# drifts back out of the model's single-premise training distribution, which
# is the same effect that made whole-chunk premises useless.
COMBINE_TOP = int(os.getenv("GROUNDING_COMBINE_TOP", "4"))


def _scoring_premises(unit: str, premises: list[str]) -> list[str]:
    """Individual premises, plus one combined window of the best few."""
    cand = _relevant_premises(unit, premises)
    if len(cand) > 1:
        cand = cand + [" ".join(cand[:COMBINE_TOP])]
    return cand


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
            cand = _scoring_premises(unit, premises)
            logits = model.predict([(p, unit) for p in cand],
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
        cand = _scoring_premises(unit, premises)
        logits = model.predict([(p, unit) for p in cand],
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

    def __init__(self, context_chunks, threshold, lexical_ok=None):
        """lexical_ok(text) -> bool is /query's _lexically_supported, passed
        in rather than imported so this module keeps no dependency on main.

        It is NOT optional in spirit. /query rescues a failed NLI score when
        the answer's numbers appear verbatim in the context, because NLI is
        unreliable on shredded table text. Without the same rescue here the
        streaming path is STRICTER than /query and refuses answers the normal
        endpoint serves -- measured: "The NV9S validator weighs 1.05 Kg on its
        own" scores 0.0179 (the qualifier "on its own" is an inference no
        single premise states) and /query serves it on lexical support.
        """
        self.context = context_chunks
        self.threshold = threshold
        self.lexical_ok = lexical_ok
        self._buf = ""
        self.released = ""
        self.min_score = 1.0
        self.failed_unit = None

    def _boundary(self, text: str) -> int:
        """Index just past the last COMPLETE sentence terminator, or -1.

        A terminator at the very end of the buffer does NOT count. Mid-stream
        the next delta has not arrived yet, so "1." is indistinguishable from
        the start of "1.05" -- and the first live test of this endpoint duly
        released "The NV9S validator weighs 1." as a finished sentence, then
        refused the rest. Only a terminator with a following character in
        hand is a real boundary; the trailing fragment is finish()'s job.
        """
        best = -1
        for i, ch in enumerate(text):
            if ch in ".!?\n":
                if i + 1 >= len(text):
                    break          # terminator is last: undecidable yet
                nxt = text[i + 1]
                if ch == "\n" or nxt.isspace():
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
            # Same second chance /query gives a failed NLI score, and for the
            # same reason: NLI is unreliable against shredded table text, so a
            # unit whose numbers all appear verbatim in the context is
            # released. Keeping this in step with /query is the whole point --
            # a streaming path that refuses what /query serves is a different
            # product, not a faster one.
            if s < self.threshold and self.lexical_ok and self.lexical_ok(u):
                continue
            if s < self.threshold:
                self.failed_unit = u
                return "", False
        self.released += candidate
        return candidate, True
