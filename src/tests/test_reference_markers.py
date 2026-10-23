"""Set anaphora ("both", "the two") is a follow-up signal.

Deliberately does NOT import _harness: the harness stubs text_utils, so a
test that loaded it would be asserting against the stub, not the patterns.

The case: "What is the power required to run both at once" matched no
reference marker, so condense_query short-circuited (llm.py returned the
query unchanged when has_reference_markers was False), a product-less
fragment hit retrieval, and the turn died. "both" referred to two products
named in the PREVIOUS turn.

That short-circuit is gone as of 2026-09-22 -- the rewriter now runs on
every turn with history and the prompt decides -- which is the real fix for
this class of miss, since a marker list can only ever be extended. These
rules still gate the deterministic combined-query fallback and feed
is_followup_turn, so set anaphora still has to be recognised; it just no
longer decides whether the rewrite happens at all.
"""
from text_utils import has_reference_markers


def test_set_anaphora_is_recognised():
    assert has_reference_markers("What is the power required to run both at once")
    assert has_reference_markers("can I use the two at the same time?")
    assert has_reference_markers("what is the draw for each of them")
    assert has_reference_markers("does the pair need separate supplies")


def test_not_length_gated():
    """The query that prompted this is 10 words, over _SHORT_QUERY_MAX_WORDS.
    Gating it on length would exclude the exact case it exists for."""
    q = "What is the power required to run both at once"
    assert len(q.split()) > 8
    assert has_reference_markers(q)


def test_assembly_wording_is_not_anaphora():
    """"together" is absent from the pattern on purpose -- these manuals are
    full of "screw it together" / "put it together"."""
    assert not has_reference_markers(
        "how many screws hold the baseplate assembly in position")
    assert not has_reference_markers(
        "how do I fit the bezel and the cashbox to the validator")


def test_standalone_questions_are_untouched():
    for q in ("what is the power draw of the SMART Coin System",
              "what note sizes does the NV9 Spectral accept",
              "how do I clean the coin path"):
        assert not has_reference_markers(q), q


def test_the_positional_index_is_gone():
    """This used to assert that _SHORT_ONLY_PATTERNS still indexed the right
    entry of _REFERENCE_PATTERNS -- a test that existed only because the
    classifier was a LIST addressed by position, which is fragile enough to
    need guarding and was wrong twice anyway (off by one on 17 Sep, and the
    length gate itself was the wrong idea).

    Both are gone as of 2026-09-19: the classifier is four named rules over
    closed word classes, and length is not consulted at all. Kept as a
    tombstone so nobody reintroduces the index.
    """
    import text_utils
    assert not hasattr(text_utils, "_REFERENCE_PATTERNS")
    assert not hasattr(text_utils, "_SHORT_ONLY_PATTERNS")
    assert not hasattr(text_utils, "_SHORT_QUERY_MAX_WORDS")


def test_length_is_not_what_decides():
    """The old gate said a pronoun counted in eight words and not in nine.
    The same question, padded, must not change its answer."""
    from text_utils import has_reference_markers as h
    assert h("does it need its own supply")
    assert h("does it need its own supply from the host board in a wall unit")
    assert not h("does the NV9 Spectral need its own supply")
    assert not h(
        "does the NV9 Spectral need its own supply from the host board too")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
