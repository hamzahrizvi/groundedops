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
) -> tuple[bool, float | None]:
    """
    Returns (is_grounded, min_entailment_score).

    is_grounded=False means at least one unit in the answer
    is not supported by the retrieved context.
    A verifier outage is distinct from an unsupported answer: it returns
    ``(False, None)``. Callers must treat that as a temporary verification
    failure, not as permission to serve an unverified answer.
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

        # One predict() per unit, and the FIRST unit under the bar ends it.
        # The verdict is the minimum over units against the threshold, so
        # once one unit fails, every later unit could only lower a score
        # that nothing downstream reads as a number: the callers branch on
        # the boolean, and the rescues (lexical, LLM verifier) do not take
        # the score. On this corpus most answers fail NLI -- the manuals
        # keep their facts in tables -- and on CPU each unit is ~12 pairs
        # at ~50ms, so a 15-unit answer that failed on unit 2 used to spend
        # ~8s scoring the rest. (Batching every pair into one predict()
        # was measured first: same verdict, no gain; the cost is per pair.)
        for unit in units:
            cand = _scoring_premises(unit, premises)
            logits = model.predict([(p, unit) for p in cand],
                                   apply_softmax=True)
            best_entailment = float(max(logits[:, col]))
            min_score = min(min_score, best_entailment)
            if min_score < threshold:
                break

        return min_score >= threshold, round(min_score, 4)

    except Exception as exc:
        logger.error(f"Grounding check failed: {exc}")
        return False, None


def score_unit(unit: str, context_chunks: list[dict]) -> float | None:
    """Entailment score for ONE unit against the context.

    Extracted so the streaming path can verify each sentence as it lands
    instead of waiting for the whole answer. check_grounding above is the
    same computation over every unit, taking the minimum -- so a sentence
    scored here and a sentence scored there get identical treatment, and
    the streamed and non-streamed paths cannot drift apart in strictness.

    Returns ``None`` when the verifier itself is unavailable. StreamGrounder
    treats that as a refusal, preserving the same fail-closed guarantee as
    the non-streaming path.
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
        return None


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
        self.verifier_unavailable = False

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
            if s is None:
                self.verifier_unavailable = True
                self.failed_unit = u
                # Keep a numeric internal score for callers that report it;
                # the separate flag makes the failure cause explicit.
                self.min_score = 0.0
                return "", False
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


# ── Contract 2: inference ────────────────────────────────────────────────
#
# check_grounding above is contract 1: every unit of the answer must be
# ENTAILED by a retrieved passage. That is the right contract for a fact
# lookup and it is why this system can be trusted on one. It is also why
#
#     "it exposes an HTTP interface on a static IP, so a Windows client on
#      the same network can reach it"
#
# can never be said. The premises are documented; the conclusion is the
# answerer's. No passage entails it, contract 1 scores it at 0.0039, and
# the visitor is told we have nothing -- while the two facts that settle
# their question sit in the cited chunk.
#
# Contract 2 does not lower the bar. It splits the answer and applies a
# DIFFERENT bar to each half:
#
#   premise     entailed by a passage, at the same threshold as contract 1.
#               Nothing is relaxed here. A premise that fails fails.
#   conclusion  not entailed -- that is what makes it a conclusion -- and
#               allowed only when every one of these holds:
#                 * every premise unit passed, and there is at least one
#                 * it is marked as a conclusion ("so", "therefore")
#                 * it is hedged ("should", "would", "appears")
#                 * the ANSWER carries an attribution line, so the reader
#                   is told which part is not from the documents
#                 * it introduces NO new content word -- the vocabulary
#                   lock, below
#                 * no passage CONTRADICTS it
#
# THE VOCABULARY LOCK IS THE LOAD-BEARING ONE. Hedging is free: a model
# that wants to invent "so the BV30 should also support polymer notes"
# will hedge it just as fluently as a sound inference. What it cannot do
# is invent it without naming something the premises never mentioned. So a
# conclusion may only RECOMBINE the words of the premises it was drawn
# from; the first token from outside that vocabulary is the tell, and it
# is a string comparison rather than a judgement, which is why it cannot
# itself be talked around.
#
# Measured against the case this was built for (Linux doc p2 as context):
#   "The ICU Lite is reachable at 192.168.137.8 over HTTP"   0.8954  premise
#   "Any host on that subnet with an HTTP client can reach"  0.0039  conclusion
#   "The ICU Lite has a built-in thermal printer"            0.0001  invention
# The middle one is the answer we want and contract 1 cannot tell it from
# the third. The vocabulary lock can: "thermal printer" is not in the
# premises and "subnet", "HTTP", "host" are.

# A unit that PRESENTS itself as drawn from the others. Anchored at the
# start: "so" mid-sentence is usually "so that", and a conclusion that
# does not announce itself as one is being passed off as documentation.
_INFERENCE_MARKER = re.compile(
    r"^\s*(?:and\s+)?(?:so|therefore|thus|hence|which means|that means|"
    r"that suggests|on that basis|in principle|it follows that|"
    r"so long as|given that)\b", re.I)

# Epistemic hedging ON THE UNIT. "a Windows client CAN reach it" is a
# claim; "SHOULD be able to reach it" is a reading. The difference is the
# whole of what we are allowed to say.
_HEDGE = re.compile(
    r"\b(should|would|likely|appears?|suggests?|presumably|expected to|"
    r"in principle|ought to|implies|may|might|probably)\b", re.I)

# Attribution ON THE WHOLE ANSWER. Per-unit hedging tells the reader the
# claim is uncertain; this tells them WHERE it came from, which is the
# thing a support engineer reading it back needs. Without it an inference
# is indistinguishable from documentation that happens to be cautiously
# worded -- and that is the failure this contract exists to prevent.
_ATTRIBUTION = re.compile(
    r"(my reading|not a stated|isn't stated|is not stated|not stated in|"
    r"not documented as|the documentation doesn't say|"
    r"the documentation does not say|inferred from|reading of the|"
    r"rather than a documented|not a documented)", re.I)

# THE THIRD ROLE, and the first version of this contract did not have it.
#
# A two-role taxonomy (premise | conclusion) says every sentence is either
# entailed by a passage or drawn from ones that are. Real answers of this
# shape contain a third kind, and the two sentences the contract itself
# REQUIRES are both of it:
#
#   "The documentation doesn't say anything about Windows."
#   "That is my reading of the network setup, not a stated claim."
#
# Neither is a claim about the product. They are claims about the CORPUS
# and about the speaker, so no passage entails them -- measured at 0.0015
# and 0.0071 against the very context they were written for. Scored as
# premises they fail, and the answer dies before the vocabulary lock has
# run. The first probe of this contract rejected every sound inference for
# that reason, and -- worse -- rejected the INVENTED ones for it too, so
# the rules that are supposed to catch invention were never exercised and
# the contract looked safe because it was refusing everything.
#
# Frames are exempt from entailment. They are not exempt from scrutiny:
#
#   * a conclusion marker wins over a frame, so "So it should work -- that
#     is my reading" is scored as the conclusion it is and faces the lock.
#     Without that precedence, attribution becomes a way to buy exemption.
#   * no clause-joining conjunction. "The documentation doesn't say, BUT
#     the BV30 has a thermal printer" is one sentence to split_units and
#     would ride in whole. A frame states what we do not have; the moment
#     it turns and asserts something, it is not a frame.
#
# A frame may name the thing we do not cover ("...about Windows") even
# though no passage mentions it. Denying coverage is safe by construction:
# the worst a wrong one can do is claim ignorance we do not have, which is
# the behaviour this whole feature exists to improve on, not a new risk.
_FRAME = re.compile(
    r"(my reading|i don'?t hold|i don'?t have|we don'?t hold|"
    r"(the )?documentation (doesn'?t|does not|do not)|"
    r"(doesn'?t|does not|don'?t) (say|cover|state|mention|specify)|"
    r"(isn'?t|is not|not) (a )?(stated|documented|covered)|"
    r"not (stated|documented|covered|specified) in)", re.I)

# A frame that turns and asserts is not a frame. Kept to the conjunctions
# that actually join an independent clause -- "and" is excluded because
# "doesn't say and doesn't imply" is still one negation.
#
# THE SEMICOLON WAS IN THIS LIST AND HAD TO COME OUT. It was added on the
# reasoning that a frame which turns mid-sentence is smuggling, and a
# semicolon joins clauses. That is true of the grammar and false of the
# usage: on the first end-to-end run against a real model (2026-09-22,
# once the gateway came back), itl-gpt-flash closed with
#
#     "This is my reading of the documentation and not a stated claim;
#      the team can confirm."
#
# which is the attribution sentence build_inference_prompt ASKS for,
# punctuated the way anyone would punctuate it. Demoted to a premise it
# scored 0.0004 -- nothing entails a sentence about the speaker -- and
# sank an otherwise sound answer. The rule was rejecting the contract's
# own required output.
#
# The conjunctions stay: "doesn't say, BUT it has a thermal printer" is
# still caught, and that is the case this guard exists for.
_FRAME_TURNS = re.compile(r"\b(but|however|although|though|whereas|yet)\b",
                          re.I)

# An answer that is mostly conclusion is not a grounded answer with a
# reading attached; it is a reading with citations attached. One.
MAX_CONCLUSIONS = int(os.getenv("INFERENCE_MAX_CONCLUSIONS", "1"))

# How strongly a passage may contradict a conclusion before it is refused.
# Deliberately strict: a conclusion is unentailed BY CONSTRUCTION, so
# entailment says nothing about it either way, and contradiction is the
# only signal left that the documents actively disagree.
CONTRADICTION_MAX = float(os.getenv("INFERENCE_CONTRADICTION_MAX", "0.25"))

_CONTRA_IDX: int | None = None


def _contradiction_index() -> int:
    """Column of the contradiction logit, resolved by label NAME.

    Same discipline as _entailment_index and for the same reason: the
    2026-08-28 bug was a hardcoded column index that had silently become
    the wrong one. A contradiction check reading the entailment column
    would refuse every sound inference and pass every contradicted one,
    and -- like that bug -- it would look fine in every metric.
    """
    global _CONTRA_IDX
    if _CONTRA_IDX is None:
        cfg = (getattr(_get_nli_model(), "config", None)
               or getattr(getattr(_get_nli_model(), "model", None),
                          "config", None))
        id2label = dict(getattr(cfg, "id2label", {}) or {})
        found = next((int(i) for i, lab in id2label.items()
                      if str(lab).lower().startswith("contradict")), None)
        if found is None:
            logger.error("NLI model exposes no 'contradiction' label (%s); "
                         "contradiction check disabled", id2label)
            found = -1
        _CONTRA_IDX = found
    return _CONTRA_IDX


def _best_premise(unit: str, premises: list[str]) -> tuple[float | None, str]:
    """score_unit, but it also says WHICH premise won.

    Identical computation and identical cost -- one predict call over the
    same candidate set -- so a premise scored here and a premise scored by
    score_unit get the same number. The extra return is the provenance the
    vocabulary lock needs: without knowing which passage entailed a
    premise, the lock has to pool every retrieved passage, and pooling is
    what let another product's table license its words.

    Returns (None, "") when the verifier is unavailable, matching
    score_unit so the caller's fail-closed branch is the same shape.
    """
    try:
        if not unit or not unit.strip():
            return 1.0, ""
        if not premises:
            return 0.0, ""
        model = _get_nli_model()
        cand = _scoring_premises(unit, premises)
        logits = model.predict([(p, unit) for p in cand], apply_softmax=True)
        col = _entailment_index()
        scores = [float(x) for x in logits[:, col]]
        i = max(range(len(cand)), key=lambda k: scores[k])
        return scores[i], cand[i]
    except Exception as exc:
        logger.error(f"_best_premise failed: {exc}")
        return None, ""


def _owners_of(premise: str, owner: dict[str, set[int]]) -> set[int]:
    """Which passages a winning premise came from.

    _scoring_premises appends a COMBINED window of the best few premises,
    and that window often wins (it is there because a generated sentence
    frequently packs several facts). It is not itself a key in `owner`, so
    it is resolved to the passages of the premises it was built from --
    every one of which genuinely supported the claim.
    """
    if premise in owner:
        return set(owner[premise])
    out: set[int] = set()
    for p, chunks in owner.items():
        if p and p in premise:
            out |= chunks
    return out


def _contradiction_score(unit: str, premises: list[str]) -> float:
    """Worst contradiction between this unit and any scored premise.

    Given ONLY the premises this answer was actually drawn from -- the
    sentences that won the entailment scoring for its premise units --
    and not every sentence of every passage they came from.

    Both narrowings were forced by the cross-product sweep, and the
    second one was the surprise. Scored against all supporting premises,
    three of eight SOUND inferences were vetoed, and the vetoing
    "premises" were: the page footer "MyCheckr User Manual - 31" (0.995),
    a section heading (0.991), and an unrelated sentence about where to
    put a cashbox (0.988). Given an unrelated pair, this model returns
    high CONTRADICTION rather than high neutral, so taking the max over
    every sentence in a chunk means any one of them can veto -- and a
    long chunk always has one.

    The rule that survives is the one that was meant all along: a
    conclusion may not contradict the premises it was drawn from. That
    still catches the real case ("the cashbox holds 300 notes" ->
    "so the cashbox should hold no notes at all", 1.00), because there
    the contradicted premise IS the one it was drawn from.
    """
    col = _contradiction_index()
    if col < 0 or not premises:
        # No premise to disagree with. Reached only if every premise unit
        # won on an empty string, which split_units makes impossible --
        # but an empty predict() raises, and a crash here would be read as
        # "contract failed" rather than "nothing to check".
        return 0.0
    model = _get_nli_model()
    cand = _scoring_premises(unit, premises)
    logits = model.predict([(p, unit) for p in cand], apply_softmax=True)
    return float(max(logits[:, col]))


# Internal dots and pluses are kept, edge punctuation is not. A first
# version used the character class [a-z0-9.+]{3,} to hold "192.168.137.8"
# together and it held the sentence-final stop together too: the premise
# yielded "connector." and the conclusion "connector", the lock called
# that a new word, and a sound inference was refused for punctuation.
_TOKEN = re.compile(r"[a-z0-9]+(?:[.+_-][a-z0-9]+)*")

# How much of a word must match for two forms to count as the same word.
# Five, because four admits "note"/"notebook" -- and the entire value of
# the lock is that a conclusion cannot name something the passages do not.
_SAME_WORD_PREFIX = 5


def _content_words(text: str) -> set[str]:
    """Stemmed content words, for the vocabulary lock.

    Stemmed via text_utils.stem so "hosts" in the conclusion matches "host"
    in the premise -- an inference that may not pluralise a noun is a rule
    nobody can write to, and the lock has to be a rule the answerer can
    actually satisfy while staying honest.
    """
    from text_utils import stem
    return {stem(w) for w in _TOKEN.findall((text or "").lower())
            if len(w) >= 3 and w not in _STOPish}


def _covered(word: str, allowed: set[str]) -> bool:
    """Is this conclusion word already in the passages' vocabulary?

    Exact stem first, then a prefix test in either direction. stem() is a
    crude suffix strip by design (its docstring says so) and does not
    bridge "connection" and "connect" -- so the passage "the host
    CONNECTION is made through a 16-way connector" did not license a
    conclusion saying "CONNECT through that same connector", and a sound
    inference was refused on morphology.

    The prefix test is the smallest thing that closes that without opening
    the lock: five characters of agreement, and one must be a prefix of
    the other. "thermal", "polymer" and "windows" share no such prefix
    with anything in a passage that does not mention them, which is the
    property being protected.
    """
    if word in allowed:
        return True
    return any(
        min(len(word), len(a)) >= _SAME_WORD_PREFIX
        and (a.startswith(word) or word.startswith(a))
        for a in allowed)


# Words a conclusion may use without them appearing in the premises. These
# are the connective and epistemic vocabulary the contract REQUIRES it to
# use -- refusing an inference for saying "should" would make the hedge
# requirement and the vocabulary lock contradict each other.
#
# Kept SMALL on purpose. Every word added here is a word an invented
# conclusion may also use for free, so the list holds connectives, hedges
# and second-person address, and no domain nouns at all. "printer",
# "windows", "polymer" are exactly what the lock is for.
_INFERENCE_VOCAB_RAW = frozenset((
    # connectives and hedges -- the vocabulary the contract REQUIRES
    "so", "therefore", "thus", "hence", "which", "means", "mean",
    "suggest", "suggests", "basis", "principle", "follow", "follows",
    "should", "would", "likely", "appear", "appears", "presumably",
    "expect", "expected", "ought", "imply", "implies", "may", "might",
    "probably", "able", "any", "all", "same", "other", "such", "both",
    "not", "but", "then", "there", "their", "them", "you", "your",
    "also", "still", "only", "own", "one", "two", "these", "those",
    # prepositions and adverbs. Locking on these produced noise, not
    # signal: the first real-corpus sweep refused a sound NV9 Spectral
    # inference for the word "within", while the invented one beside it
    # was caught on "step-down regulator". A lock that fires on "within"
    # buries the tells it exists to surface.
    "within", "over", "through", "once", "after", "before", "between",
    "into", "onto", "under", "above", "during", "while", "when", "where",
    "than", "about", "per", "out", "off", "well", "each", "least", "most",
    "since", "unless", "because", "whether", "either", "neither",
    # light verbs. Generic enough that every answer uses them and none of
    # them names anything.
    "work", "works", "use", "used", "using", "run", "runs", "need",
    "needs", "give", "gives", "take", "takes", "get", "gets", "make",
    "makes", "come", "comes", "keep", "allow", "allows", "does", "done",
    "being", "been", "having", "say", "says", "said",
    # DELIBERATELY ABSENT, and this is the list that matters: connect,
    # support, accept, drive, supply, host, reach, pair, share, fit,
    # install, configure -- and every noun. Those are the words a
    # compatibility claim is MADE of, so a conclusion using one must have
    # got it from a passage that supported a premise.
))
_INFERENCE_VOCAB: set[str] | None = None


def _inference_vocab() -> set[str]:
    """_INFERENCE_VOCAB_RAW, stemmed to match _content_words."""
    global _INFERENCE_VOCAB
    if _INFERENCE_VOCAB is None:
        from text_utils import stem
        _INFERENCE_VOCAB = {stem(w) for w in _INFERENCE_VOCAB_RAW}
    return _INFERENCE_VOCAB


def check_inference(
    answer: str,
    context_chunks: list[dict],
    threshold: float = 0.35,
) -> tuple[bool, dict]:
    """Contract 2. Returns (is_acceptable, report).

    The report is the point of the return shape: an operator asking "why
    was this served?" or "why was this refused?" gets a per-unit answer
    with the role, the score and the rule that decided it, rather than one
    number. sweep_grounding-style triage over a corpus of inference cases
    is the intended use.

        {"ok": bool,
         "reason": str,                       # why, when not ok
         "units": [{"text", "role", "score", "ok", "why"}],
         "premises": int, "conclusions": int, "frames": int}

    role is one of premise | conclusion | frame -- see _FRAME above for
    why the third one exists and what it is still held to.

    FAILS CLOSED, like check_grounding: a verifier outage (_best_premise
    returning None) is not permission to serve an unverified inference. It
    is MORE important here than on contract 1, because this is the one
    path where the answer says something the documents do not.
    """
    report = {"ok": False, "reason": "", "units": [],
              "premises": 0, "conclusions": 0, "frames": 0}
    try:
        units = split_units(answer)
        if not units:
            report["ok"], report["reason"] = True, "nothing to check"
            return True, report

        context_texts = [c["text"] if isinstance(c, dict) else str(c)
                         for c in context_chunks]
        premises = _premises(context_texts) if context_texts else []
        if not premises:
            report["reason"] = "no context to reason from"
            return False, report

        attributed = bool(_ATTRIBUTION.search(answer or ""))
        # Per-chunk premises, so the lock can be scoped to the passages
        # that actually supported this answer -- see below.
        per_chunk, owner = [], {}
        for i, t in enumerate(context_texts):
            ps = _premises([t])
            per_chunk.append(ps)
            for p in ps:
                owner.setdefault(p, set()).add(i)

        # ROLE ASSIGNMENT, in this order. A conclusion marker wins over a
        # frame so attribution cannot buy an exemption from the lock; a
        # frame that turns and asserts falls back to being a premise and
        # is scored as one, which is the strict reading.
        premise_units, conclusion_units, frame_units = [], [], []
        for u in units:
            if _INFERENCE_MARKER.search(u):
                conclusion_units.append(u)
            elif _FRAME.search(u) and not _FRAME_TURNS.search(u):
                frame_units.append(u)
            else:
                premise_units.append(u)
        report["premises"] = len(premise_units)
        report["conclusions"] = len(conclusion_units)
        report["frames"] = len(frame_units)
        for u in frame_units:
            report["units"].append({"text": u, "role": "frame", "score": None,
                                    "ok": True, "why": ""})

        # ── every premise, at the ordinary bar ──────────────────────────
        #
        # `supporting` is the set of passages that actually carried a
        # premise of THIS answer, and it is what the vocabulary lock is
        # scoped to. Pooling every retrieved passage instead was a hole
        # big enough to drive the feature through: asked "does the BV30
        # work with polymer notes", retrieval returns the BV30 cashbox
        # section AND the NV200S media-requirements table, which lists
        # "Polymer notes". Pooled, that table licenses the word "polymer"
        # in a conclusion about the BV30 -- so "So the BV30 should also
        # accept polymer notes" passes a lock whose entire job is to stop
        # exactly that. Scoped, the NV200S table supports no premise here,
        # contributes no vocabulary, and the conclusion is refused.
        #
        # Found by running the cross-product sweep against the real index
        # rather than hand-built chunks. Nothing in the unit tests could
        # have shown it: it needs a retrieval that pulls two products.
        premises_ok = True
        supporting: set[int] = set()
        drawn_from: list[str] = []
        for u in premise_units:
            score, best = _best_premise(u, premises)
            if score is None:
                report["units"].append({"text": u, "role": "premise",
                                        "score": None, "ok": False,
                                        "why": "verifier unavailable"})
                report["reason"] = "verifier unavailable"
                return False, report
            ok = score >= threshold
            premises_ok = premises_ok and ok
            if ok:
                supporting |= _owners_of(best, owner)
                if best:
                    drawn_from.append(best)
            report["units"].append({
                "text": u, "role": "premise", "score": round(score, 4),
                "ok": ok, "why": "" if ok else "not entailed by any passage"})

        # Vocabulary available to a conclusion: the supporting passages,
        # and nothing else. NOT the question either -- letting the
        # question feed the lock would mean "does it work with Windows?"
        # licenses the word "Windows" in the conclusion, which is the
        # claim we have no grounds for.
        allowed = set()
        for p in premises:
            if owner.get(p, set()) & supporting:
                allowed |= _content_words(p)

        if not conclusion_units:
            # No conclusion: this is contract 1, reached through contract
            # 2's door. Same verdict it would have got there.
            report["ok"] = premises_ok
            report["reason"] = "" if premises_ok else "a premise is ungrounded"
            return premises_ok, report

        if not premises_ok:
            report["reason"] = ("a premise is ungrounded, so nothing may be "
                                "concluded from it")
            return False, report
        if not premise_units:
            report["reason"] = "a conclusion with no premises is a bare claim"
            return False, report
        if len(conclusion_units) > MAX_CONCLUSIONS:
            report["reason"] = (f"{len(conclusion_units)} conclusions "
                                f"(limit {MAX_CONCLUSIONS})")
            return False, report
        if not attributed:
            report["reason"] = ("the answer does not say which part is a "
                                "reading rather than documentation")
            return False, report

        # ── each conclusion, at the inference bar ───────────────────────
        for u in conclusion_units:
            why = ""
            if not _HEDGE.search(u):
                why = "unhedged conclusion"
            else:
                vocab = allowed | _inference_vocab()
                new = {w for w in _content_words(u)
                       if not _covered(w, vocab)}
                if new:
                    why = ("introduces " +
                           ", ".join(sorted(new)[:4]) + " -- not in any passage")
                else:
                    contra = _contradiction_score(u, drawn_from)
                    if contra > CONTRADICTION_MAX:
                        why = f"contradicted by a passage ({contra:.2f})"
            report["units"].append({"text": u, "role": "conclusion",
                                    "score": None, "ok": not why, "why": why})
            if why:
                report["reason"] = why
                return False, report

        report["ok"] = True
        return True, report

    except Exception as exc:
        logger.error(f"Inference check failed: {exc}")
        report["reason"] = f"inference check failed: {exc}"
        return False, report
