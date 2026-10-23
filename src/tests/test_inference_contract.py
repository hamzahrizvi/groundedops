"""Contract 2 -- what an answer must satisfy to say something the
documents do not say outright.

    "does ICU Lite work with Windows?"

    contract 1:  I don't have that in the product documentation.
    contract 2:  The documentation doesn't cover Windows. It does say ICU
                 Lite is reachable at a static 192.168.137.8 over HTTP. So
                 any host on that subnet should be able to reach it. That
                 is my reading of the network setup, not a stated claim.

The second is useful and is not in any manual, which is precisely why it
needs a gate of its own rather than a lower threshold on the existing one.
These tests are that gate's specification.

THE NLI MODEL IS FAKED HERE, deliberately. Every rule this file pins --
role assignment, the hedge, the attribution line, the vocabulary lock,
the frame that turns and asserts, the scoping of the lock to supporting
passages -- is a decision the contract makes ABOUT the model's scores,
not a property of the scores. Faking entailment makes those decisions
testable in milliseconds and keeps the suite from depending on a 180 MB
download. The real model over the real index is exercised by
tests/sweep_inference_products.py, which is where a regression in the
scores themselves would show up.
"""
import os
import sys

import numpy as np

# The path is set explicitly rather than by importing _harness, which is
# how most files here get it. _harness STUBS text_utils, and this
# contract leans on two real functions from it -- split_units for the
# unit split and stem for the vocabulary lock. Under the stub these
# tests would be asserting on the stub.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grounding  # noqa: E402


class FakeNLI:
    """Scores entailment by word overlap, contradiction by a marker.

    Overlap rather than a fixed table because PROVENANCE is under test:
    the vocabulary lock is scoped to the passages that actually entailed
    a premise, so which premise wins has to depend on what the premise
    says. A fake that returned the same score for every pair would let
    the wrong chunk be credited and the scoping tests would pass while
    the lock leaked.
    """

    class config:
        id2label = {0: "contradiction", 1: "entailment", 2: "neutral"}

    def __init__(self, contradicts=()):
        self.contradicts = tuple(contradicts)

    @staticmethod
    def _words(s):
        return {w for w in s.lower().replace(",", " ").replace(".", " ").split()
                if len(w) > 2}

    def predict(self, pairs, apply_softmax=True):
        rows = []
        for premise, hyp in pairs:
            hw = self._words(hyp)
            overlap = len(hw & self._words(premise)) / max(len(hw), 1)
            entail = 0.99 if overlap >= 0.6 else round(overlap / 10, 4)
            contra = 0.99 if any(c in hyp for c in self.contradicts) else 0.01
            rows.append([contra, entail, max(0.0, 1 - entail - contra)])
        return np.array(rows)


def use_fake(monkeypatch_targets=None, contradicts=()):
    """Install the fake and clear the caches keyed to the real model."""
    grounding._nli_model = FakeNLI(contradicts)
    grounding._ENTAIL_IDX = None
    grounding._CONTRA_IDX = None
    return grounding._nli_model


def check(answer, chunks, contradicts=()):
    use_fake(contradicts=contradicts)
    try:
        return grounding.check_inference(answer, chunks)
    finally:
        grounding._nli_model = None
        grounding._ENTAIL_IDX = None
        grounding._CONTRA_IDX = None


LINUX = [{"text": "The ICU Lite presents a network interface at the static "
                  "address 192.168.137.8 over HTTP. Any host on the same "
                  "subnet can open a session to that address."}]

FRAME = "That is my reading of the network setup, not a stated claim."
PREMISE = ("The ICU Lite presents a network interface at the static address "
           "192.168.137.8 over HTTP")
CONCLUSION = ("So any host on the same subnet should be able to open a "
              "session to that address")


def sound():
    return f"{PREMISE}. {CONCLUSION}. {FRAME}"


# ── what the contract allows ─────────────────────────────────────────

def test_a_hedged_attributed_recombination_is_served():
    ok, rep = check(sound(), LINUX)
    assert ok, rep["reason"]
    roles = {u["role"] for u in rep["units"]}
    assert roles == {"premise", "conclusion", "frame"}


def test_a_lookup_with_no_conclusion_is_contract_one_unchanged():
    """Contract 2 must not become a second way to serve a plain answer at
    a different bar. With no conclusion it IS contract 1."""
    ok, _ = check(f"{PREMISE}.", LINUX)
    assert ok
    ok, rep = check("The ICU Lite has a built-in thermal printer.", LINUX)
    assert not ok and rep["conclusions"] == 0


# ── what it refuses, and on which rule ───────────────────────────────

def test_the_vocabulary_lock_catches_an_invented_component():
    """The load-bearing rule. Hedging is free -- a model inventing a
    driver will hedge it as fluently as a sound inference -- but it
    cannot invent one without naming something no passage mentions."""
    ok, rep = check(
        f"{PREMISE}. So a Windows client with the bundled ICU desktop "
        f"driver should be able to reach it. {FRAME}", LINUX)
    assert not ok
    assert "introduces" in rep["reason"]
    assert "desktop" in rep["reason"] or "bundled" in rep["reason"]


def test_an_unhedged_conclusion_is_refused():
    ok, rep = check(
        f"{PREMISE}. So any host on the same subnet can open a session to "
        f"that address. {FRAME}", LINUX)
    assert not ok and rep["reason"] == "unhedged conclusion"


def test_an_unattributed_answer_is_refused():
    """Per-unit hedging says the claim is uncertain; the attribution line
    says where it came from. Without it an inference is indistinguishable
    from cautiously-worded documentation."""
    ok, rep = check(f"{PREMISE}. {CONCLUSION}.", LINUX)
    assert not ok and "reading" in rep["reason"]


def test_an_ungrounded_premise_sinks_the_conclusion_above_it():
    ok, rep = check(
        f"The ICU Lite ships with a signed Windows kernel driver. "
        f"{CONCLUSION}. {FRAME}", LINUX)
    assert not ok and "premise is ungrounded" in rep["reason"]


def test_a_conclusion_with_no_premise_is_a_bare_claim():
    ok, rep = check(f"{CONCLUSION}. {FRAME}", LINUX)
    assert not ok and "no premises" in rep["reason"]


def test_a_conclusion_contradicting_its_own_premise_is_refused():
    ok, rep = check(
        f"{PREMISE}. So the ICU Lite should present no network interface "
        f"at all. {FRAME}", LINUX, contradicts=("no network interface",))
    assert not ok and "contradicted" in rep["reason"]


def test_more_than_one_conclusion_is_refused():
    """An answer that is mostly conclusion is not a grounded answer with
    a reading attached; it is a reading with citations attached."""
    ok, rep = check(
        f"{PREMISE}. {CONCLUSION}. So it should also suit a kiosk. {FRAME}",
        LINUX)
    assert not ok and "conclusions" in rep["reason"]


# ── the third role ───────────────────────────────────────────────────

def test_the_honesty_sentences_are_not_scored_as_premises():
    """"The documentation doesn't say" and "that is my reading" are
    claims about the CORPUS, not about the product. No passage entails
    them -- measured at 0.0015 and 0.0071 against the very context they
    were written for -- so scoring them as premises refused every sound
    inference, INCLUDING the invented ones, which is how the first
    version of this contract looked safe while testing nothing."""
    ok, rep = check(
        f"The documentation doesn't say anything about Windows. {PREMISE}. "
        f"{CONCLUSION}. {FRAME}", LINUX)
    assert ok, rep["reason"]
    assert rep["frames"] == 2 and rep["premises"] == 1


def test_a_frame_that_turns_and_asserts_is_not_a_frame():
    """Otherwise "the documentation doesn't say, BUT it has a thermal
    printer" rides in whole -- split_units keeps it as one sentence."""
    ok, rep = check(
        f"{PREMISE}. The documentation doesn't say, but the ICU Lite has a "
        f"built-in thermal printer. {CONCLUSION}. {FRAME}", LINUX)
    assert not ok
    assert any(u["role"] == "premise" and "thermal" in u["text"]
               for u in rep["units"])


def test_a_conclusion_marker_beats_an_attribution_phrase():
    """Attribution must not be a way to buy exemption from the lock: a
    sentence that concludes AND attributes is scored as the conclusion."""
    ok, rep = check(
        f"{PREMISE}. So my reading is that a bundled desktop driver should "
        f"reach it. {FRAME}", LINUX)
    assert not ok and "introduces" in rep["reason"]


# ── the lock is scoped, not pooled ───────────────────────────────────

BV30_AND_NV200S = [
    {"text": "The BV30 does not have a built-in cashbox. It is designed to "
             "be used in stacker-less free fall applications."},
    {"text": "The NV200 Spectral media that can be accepted includes Paper "
             "notes Polymer notes Windowed notes Barcoded tickets."},
]


def test_another_products_passage_does_not_license_its_words():
    """THE CROSS-PRODUCT LEAK. Asked "does the BV30 work with polymer
    notes", retrieval returns the BV30 cashbox section AND the NV200S
    media table, which lists "Polymer notes". Pooling every retrieved
    passage into the lock lets that table license the word "polymer" in a
    conclusion about the BV30 -- so the invention passes the rule whose
    whole job is to stop it.

    Only passages that actually supported a premise of THIS answer
    contribute vocabulary. The NV200S table supports nothing here.

    Found against the real index; no hand-built chunk would have shown
    it, because it needs a retrieval that pulls two products at once.
    """
    ok, rep = check(
        "The BV30 does not have a built-in cashbox. So the BV30 should also "
        "accept polymer notes. That is my reading, not a stated claim.",
        BV30_AND_NV200S)
    assert not ok, "the NV200S media table must not license 'polymer'"
    assert "polym" in rep["reason"]


def test_a_conclusion_from_the_supporting_passage_is_still_allowed():
    """The scoping must not become a refuse-everything rule: words from
    the passage that DID support the premise remain available."""
    ok, rep = check(
        "The BV30 does not have a built-in cashbox. So the BV30 should be "
        "designed for stacker-less applications. That is my reading, not a "
        "stated claim.", BV30_AND_NV200S)
    assert ok, rep["reason"]


# ── fails closed ─────────────────────────────────────────────────────

def test_a_verifier_outage_is_not_permission_to_infer():
    """More important here than on contract 1: this is the one path that
    says something the documents do not."""
    grounding._nli_model = None
    grounding._ENTAIL_IDX = None
    import unittest.mock as mock
    with mock.patch.object(grounding, "_get_nli_model",
                           side_effect=RuntimeError("model unavailable")):
        ok, rep = grounding.check_inference(sound(), LINUX)
    assert ok is False
    assert "unavailable" in rep["reason"] or "failed" in rep["reason"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
