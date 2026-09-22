"""The two rule-based classifiers, and the traps they have to avoid.

Both were rewritten on 2026-09-19 from lists of observed phrasings into
general rules, because ten scripted customer conversations found four gaps
in an afternoon and a list of things customers have already said cannot
cover what the next one says.

  has_reference_markers   four rules over closed word classes (R1 opener,
                          R2 pro-form, R3 discourse deixis, R4 continuation)
  is_commercial_question  the money/commerce semantic field, matched on
                          stems rather than whole words

A general rule buys coverage and pays for it in false positives, so most of
this file is the false positives -- the words in THIS corpus that look
commercial or referential and are not. Each one here was a real miss during
the rewrite, not a hypothetical.

Does NOT import _harness: the harness stubs both of these.
"""
from text_utils import has_reference_markers as ref, why_reference_markers as why
from sales import is_commercial_question as commercial


# ── the four rules, each on wording that no earlier version had seen ──

def test_r1_stacked_openers():
    """People stack connectives in chat. The old pattern allowed exactly
    one, so "ok what about" matched and "ok and what about" did not."""
    for q in ("and what about the bezel", "ok and what about the bezel",
              "right, so what about the bezel", "then how about the bezel",
              "well and what about the bezel"):
        assert why(q) == "R1 opener", (q, why(q))


def test_r2_subject_pronoun_at_any_length():
    """The length gate is gone. What decides is whether the pro-form is the
    SUBJECT -- i.e. whether the question can be read at all without the
    previous turn."""
    assert ref("is it noisy")
    assert ref("does it need a separate supply from the host board")
    assert ref("how many coins a second can it pay out")
    assert ref("is that configurable from the host or only in firmware")


def test_r2_object_pronoun_is_not_enough():
    """"it" in object position leaves the question readable on its own."""
    for q in ("can my staff use it without any training",
              "how do I empty the cash out of it at the end of the day",
              "what happens if someone feeds it a torn fiver"):
        assert not ref(q), (q, why(q))


def test_r2_a_named_product_makes_a_question_self_contained():
    """This is what replaced counting words: the question names its own
    subject, so a stray pro-form does not make it a follow-up."""
    assert not ref("is the BV30 any good with these new polymer notes")
    assert not ref("what note denominations does the NV9 Spectral accept")
    assert ref("is it any good with these new polymer notes")


def test_r2_expletive_it_is_not_a_pro_form():
    """Impersonal "it" refers to nothing: "how long does IT take to..."."""
    for q in ("how long does it take to count a full cashbox of mixed notes",
              "is it safe to pull a jammed note out by hand",
              "how long does it take to clear a jam"):
        assert not ref(q), (q, why(q))


def test_r2_an_antecedent_in_the_same_sentence_resolves_it():
    """Subordinate or coordinate, the question can answer itself."""
    for q in ("if the machine rejects a note does it give any indication why",
              "when the cashbox is full does it stop taking notes or keep going",
              "once the lid is closed does it lock automatically",
              "does the hopper need emptying manually or does it self-level"):
        assert not ref(q), (q, why(q))
    # ...but the same clause alone, with nothing before it, is a follow-up.
    assert ref("does it lock automatically")


def test_r2_pronominal_one_needs_an_elided_noun_phrase():
    """"one" is a pro-form in "which ONE", a numeral in "share ONE bus",
    and part of an idiom in "day ONE"."""
    assert ref("which one would you recommend for a corner shop")
    assert ref("is the mini one any smaller to mount")
    assert not ref("can two units share one RS232 bus")
    assert not ref("do I need anything else to get it working on day one")
    assert not ref("where can I buy one and how soon can you deliver")


def test_r3_discourse_deixis_in_both_number_forms():
    assert ref("from step 2 onward is the wiring the same")
    assert ref("from step two onward is the wiring the same")
    assert ref("is the loom the same as mentioned above")


def test_r4_continuation():
    assert ref("tell me more")
    assert ref("can you please tell me more about it")
    assert ref("more detail on that")


def test_standalone_questions_from_both_registers():
    """An installer naming parts and a shop owner describing an outcome.
    Neither leans on a previous turn."""
    for q in ("what baud rate does the serial link default to",
              "which pinout do I need for the IF5 interface cable",
              "can two units share one RS232 bus",
              "post installation verification installer sign off",
              "how do I clean the coin path",
              "what is the capital of france"):
        assert not ref(q), (q, why(q))


# ── the commercial field, and the words in this corpus that mimic it ──

def test_commercial_covers_the_field_not_a_word_list():
    """None of these were in the list version; all are the same question
    ("what will this cost me") in different words."""
    for q in ("are there any recurring fees for using it",
              "is there a monthly subscription",
              "what is the licensing cost per device",
              "can we lease them instead of buying",
              "is it expensive",
              "what is the lead time on fifty units",
              "who is your distributor in Ireland",
              "is there an extra charge for support",
              "how much is it",
              "whats the MOQ"):
        assert commercial(q), q


def test_commercial_does_not_fire_on_the_manuals_own_vocabulary():
    """Every one of these is a documentation question that happens to use a
    word from the money field. "feeds" in particular broke scenario 03: the
    "fee" stem matched it, and a note-handling question was routed to sales.
    """
    for q in ("how much does it weigh",
              "how much power does it draw",
              "what happens if someone feeds it a torn fiver",
              "how do I feed a note in by hand",
              "does it pay out coins in mixed denominations",
              "how many coins a second can it pay out",
              "what baud rate does the serial link default to",
              "what is the acceptance rate for polymer notes",
              "how long does it take to charge the battery",
              "in order to fit the bezel what do I need",
              "how do I free the note path of debris",
              "what is the note delivery mechanism"):
        assert not commercial(q), q


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
