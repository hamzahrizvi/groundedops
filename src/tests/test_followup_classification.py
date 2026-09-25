"""Which turns count as follow-ups — on questions the pipeline has never seen.

Two bugs met here on 2026-09-17 and both made a FRESH question look like a
follow-up, which is what routes it to a clarifying question about the
previous topic instead of an answer:

  * _SHORT_ONLY_PATTERNS pointed at the wrong pattern, so any question
    containing "it" anywhere counted, however long and however standalone.
  * main.py passed is_followup_turn the query AFTER the selected product had
    been appended for retrieval, so `resolved != raw` was true on nearly
    every scoped turn.

Every question below is new — none appears in eval_cases*.json, logs.jsonl or
another test — and they are split between someone who installs these machines
for a living and someone who has never seen one. The phrasing gap is the
point: the installer writes in product vocabulary the patterns rarely match,
the newcomer writes in pronouns and vague nouns, which is where a follow-up
detector either earns its keep or misfires.

Deliberately does NOT import _harness: the harness stubs text_utils.
"""
from text_utils import has_reference_markers, is_followup_turn

# Any non-empty history: these functions only check that a conversation
# exists, not what is in it.
HIST = [{"q": "what voltage does the NV9USB+ need", "a": "12 V DC nominal."}]


# ── standalone questions, from someone who has never used the product ──

NEWCOMER_STANDALONE = [
    "how long does it take to count a full cashbox of mixed notes",
    "if the machine rejects a note does it give any indication why",
    "do I need anything else to get it working on day one",
    "is there much noise when a note goes in",
    "can staff use this without any training",
]


# ── standalone questions, from an installer or integrator ──

TECHNICAL_STANDALONE = [
    "what is the current draw on the 12V rail during a stacking cycle",
    "does the firmware ship with SSP encryption enabled",
    "which pinout do I need for the IF5 interface cable",
    "can two units share one RS232 bus",
    "what is the recommended service interval for the belt assembly",
    "is the bezel aperture the same across the NV9 range",
]


def test_a_newcomers_fresh_question_is_not_a_follow_up():
    """Four of these contain "it" or "this". None refers to a previous turn:
    "it" is the machine, not something we said. Before the gate was fixed,
    the two containing "it" were classified as follow-ups and a refusal on
    them produced a clarifying question about whatever was asked before."""
    for q in NEWCOMER_STANDALONE:
        assert not has_reference_markers(q), q
        assert not is_followup_turn(q, HIST, q), q


def test_an_installers_fresh_question_is_not_a_follow_up():
    for q in TECHNICAL_STANDALONE:
        assert not has_reference_markers(q), q
        assert not is_followup_turn(q, HIST, q), q


# ── genuine follow-ups, both audiences ──

def test_genuine_follow_ups_still_register():
    for q in (
        # Listed as standalone when this file was written, and moved here on
        # 2026-09-19: "will THIS fit" cannot be read without knowing what
        # "this" is, so the original label was the weak one. The rule-based
        # classifier disagreeing with a hand label is worth checking both
        # ways round, and here the rule was right.
        "will this fit under a standard shop counter",
        # newcomer: pronouns and vague quantifiers, no product named
        "those need their own plug?",
        "ok and what about the smaller one",
        "tell me more about that",
        "can both run off one socket",
        # installer: terse, references the previous answer's material
        "same for the two of them?",
        "what about that in a humid room",
        "from step 3 onwards, is the loom the same",
    ):
        assert has_reference_markers(q), q
        assert is_followup_turn(q, HIST, q), q


def test_a_sentence_initial_pronoun_counts_however_long_the_sentence():
    """The length gate used to sit on this pattern, so a newcomer describing
    a fault in one long sentence -- which is how people actually describe
    faults -- stopped registering as a follow-up past eight words."""
    q = "it keeps rejecting the same note even after I cleaned the note path"
    assert len(q.split()) > 8
    assert has_reference_markers(q)


def test_a_pronoun_buried_in_a_long_question_does_not_count():
    """The mirror image, and the one that was firing wrongly: "it" deep in a
    long, fully self-contained question is about the machine, not about
    anything said earlier."""
    for q in ("how long does it take to count a full cashbox of mixed notes",
              "if a note jams is it safe to pull it out by hand",
              "when the cashbox is full does it stop taking notes or keep going"):
        assert len(q.split()) > 8
        assert not has_reference_markers(q), q


def test_a_short_buried_pronoun_still_counts():
    """The gate is a length gate, not a removal: in a short fragment a
    pronoun really is doing referential work."""
    for q in ("is it heavy?", "how wide are they", "can I clean them"):
        assert len(q.split()) <= 8
        assert has_reference_markers(q), q


# ── the wiring bug, characterised ──

def test_product_context_must_not_be_what_makes_a_turn_a_follow_up():
    """main.py appends the selected product to the query before retrieval
    ("Added product context ->"). is_followup_turn reads `resolved != raw` as
    evidence the CONVERSATION rewrote the question, so handing it the
    retrieval-rewritten string makes every scoped turn a follow-up.

    This test pins the trap rather than the fix: it shows the wrong argument
    producing the wrong answer on a question that is plainly standalone.
    tests/test_clarify_gate.py pins that main.py passes the right one.

    UPDATED 2026-09-22. The trap no longer springs on an UNRELATED product,
    because the evidence test now asks where the rewrite's added words came
    from and "Spectral" appears nowhere in the history. That is a second,
    independent guard, not a replacement for the first: it holds only while
    the appended product is not the one already under discussion. Append the
    product a scoped chat is actually about -- the normal case -- and the
    string is indistinguishable from a genuine resolution. So main.py
    freezing condensed_query before the retrieval rewrite remains the fix;
    this is the belt to its braces, and the second assertion below pins the
    residual exposure rather than pretending it is gone.
    """
    q = "what is the recommended service interval for the belt assembly"

    # An unrelated product: the added word is not in the history, so the
    # retrieval rewrite is correctly not mistaken for the conversation's.
    assert is_followup_turn(q, HIST, f"{q} (NV9 Spectral)") is False,         "product context alone must not make a standalone turn a follow-up"

    # The product ALREADY under discussion: still indistinguishable, which
    # is why the caller must not pass this string at all.
    assert "NV9USB+" in HIST[0]["q"]
    assert is_followup_turn(q, HIST, f"{q} (NV9USB+ validator)") is True,         "residual trap: the appended product is in the history"

    assert is_followup_turn(q, HIST, q) is False,         "given the pre-retrieval query, the same turn is correctly standalone"


def test_the_condensers_own_rewrite_still_counts():
    """The flip side: when the CONVERSATION rewrote the question, that is a
    real follow-up signal and must survive the fix. A newcomer's "and the
    other one?" resolved against the previous turn is exactly the case the
    clarify gate exists for."""
    raw = "and the other one?"
    resolved = "what voltage does the NV9 Spectral need"
    assert is_followup_turn(raw, HIST, resolved) is True


def test_no_history_is_never_a_follow_up():
    """A first message that happens to open with a pronoun is a malformed
    standalone question, not a follow-up -- there is nothing to follow."""
    assert is_followup_turn("those need their own plug?", [], "x") is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")


# ── The rewrite is no longer gated by a regex (2026-09-22) ──────────────
#
# condense_query used to skip the model unless has_reference_markers matched.
# That gate is gone: the prompt already instructs "if already self-contained,
# return it EXACTLY AS-IS", and a surface-marker list can only ever be
# extended -- it is what killed "what is the power required to run both at
# once", where no marker matched so no rewrite ran.
#
# Removing it puts weight on is_followup_turn that it could not carry. Its
# second clause was `resolved_query != raw_query`, which counted ANY string
# difference as proof the conversation was needed. With the rewriter running
# on every turn that is no longer rare: _normalize_query alone lowercases
# "DEFAULT LOGIN???", and a model told to return a question unchanged still
# reflows and repunctuates it. A fresh question would then read as a
# follow-up and be answered with a clarifying question about the previous
# topic -- the 2026-09-19 fault, through a different door.

def test_a_cosmetic_rewrite_is_not_evidence_of_a_follow_up():
    for resolved in (
        "How sturdy are NV9 ST?",              # recased and repunctuated
        "how sturdy are nv9 st",               # unchanged
        "how  sturdy   are nv9 st",            # reflowed
        "how sturdy are nv9 st.",              # trailing punctuation
    ):
        assert not is_followup_turn("how sturdy are nv9 st", HIST, resolved), resolved


def test_normalisation_alone_is_not_evidence_of_a_follow_up():
    """main.py hands condense_query the NORMALIZED query, so the string it
    gets back differs from the raw one whenever normalisation did anything
    -- before the rewriter has made any judgement at all."""
    assert not is_followup_turn("DEFAULT LOGIN CREDENTIALS???", HIST,
                                "default login credentials")


def test_a_rewrite_that_pulled_words_from_history_is_evidence():
    """The other direction: this is what condensation succeeding looks like,
    and it must still register even with no surface marker in the raw
    query."""
    hist = [{"q": "how do I reset the MyConnect Hub",
             "a": "Hold the recessed button for ten seconds."}]
    assert is_followup_turn("and the app?", hist,
                            "how do I reset the MyConnect app")


def test_words_the_rewrite_invented_are_not_evidence():
    """A rewrite that added words found nowhere in the two turns the model
    was shown did not resolve anything against the conversation -- it
    elaborated, which is not the same claim."""
    hist = [{"q": "what voltage does the NV9USB+ need", "a": "12 V DC nominal."}]
    assert not is_followup_turn(
        "how long does a full cashbox take to count", hist,
        "how long does a completely full cashbox take to count notes")
