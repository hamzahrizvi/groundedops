"""Set anaphora ("both", "the two") is a follow-up signal.

Deliberately does NOT import _harness: the harness stubs text_utils, so a
test that loaded it would be asserting against the stub, not the patterns.

The case: "What is the power required to run both at once" matched no
reference marker, so condense_query short-circuited (llm.py returns the
query unchanged when has_reference_markers is False), a product-less
fragment hit retrieval, and the turn died. "both" referred to two products
named in the PREVIOUS turn.
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


def test_appended_so_the_short_only_index_still_points_where_it_did():
    """_SHORT_ONLY_PATTERNS indexes _REFERENCE_PATTERNS by POSITION, so a
    marker inserted mid-list would silently re-target the length gate onto
    a different pattern."""
    import text_utils
    assert max(text_utils._SHORT_ONLY_PATTERNS) < len(text_utils._REFERENCE_PATTERNS) - 1
    last = text_utils._REFERENCE_PATTERNS[-1].pattern
    assert "both" in last, "the new marker must be last"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
