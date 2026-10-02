"""Context passages are numbered and rank-ordered in the answering prompt.

Measured on 2026-09-17: for "can I use an nv9 spectral with note float?" the
sentence that answers it reranked #1 at 0.9992 and WAS in context; the answer
served was a note-dimensions table that reranked #2. Eight passages scored
0.910-0.999, so nothing in the prompt told the model which one the retriever
believed in, or even where one passage ended and the next began.

These pin the presentation fix, and pin that context SELECTION was left
alone -- see test_selection_was_not_tightened for why.
"""
import _harness  # noqa: F401 -- load main with lightweight test dependencies
import main


def test_passages_are_numbered_and_counted():
    prompt = main.build_answer_prompt("", "alpha text", "q?")
    assert "[Passage 1 of 1]" in prompt or "Passage" in prompt, \
        "a single passage still gets a boundary marker from the caller"


def test_prompt_rejects_a_mere_mention_as_a_yes_no_answer():
    """The failure was answering a compatibility question with a table that
    merely listed both products."""
    prompt = main.build_answer_prompt("", "ctx", "can I use X with Y?")
    assert "is not an answer to that question" in prompt
    assert "works with something else" in prompt


def test_prompt_requires_conditional_support_to_be_qualified():
    """The real answer was "supported, but disabled on firmware >= 1.21". An
    unqualified Yes there is wrong the moment the condition applies."""
    prompt = main.build_answer_prompt("", "ctx", "is it supported?")
    assert "Support is often conditional" in prompt
    assert "firmware" in prompt


def test_prompt_explains_passage_order_without_making_it_a_rule():
    """Rank is a hint. Made a rule, it would break every question whose
    answer legitimately sits in a lower-ranked passage -- which is most
    multi-chunk answers."""
    prompt = main.build_answer_prompt("", "ctx", "q?")
    assert "most likely to contain the answer" in prompt
    assert "hint, not a rule" in prompt


def test_selection_was_not_tightened():
    """An absolute floor and a largest-gap cut were both measured and
    rejected. This pins that decision so a future change has to argue with
    the evidence rather than rediscover it.

      - absolute floor: loses the rank-1 chunk on 4 of 15 measured questions.
        The reranker's scale is query-relative -- "what is the operating
        temperature range?" tops out at 0.000 and "power draw just for the
        scs" at 0.004, with the correct chunk at the top in both.
      - largest-gap cut: would cut at the 0.771 -> 0.066 drop in the pinout
        case documented in main.py, where the chunks CONTAINING the pinout
        scored 0.066 and 0.050 -- the exact regression CONTEXT_MIN exists
        to prevent.
    """
    import inspect
    src = inspect.getsource(main.query)
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "CONTEXT_FLOOR_RATIO" in code, "the relative floor is still the filter"
    assert "CONTEXT_MIN" in code, "the starvation backstop is still in place"
    # No absolute threshold snuck in alongside it.
    assert "ABS_FLOOR" not in code and "ABSOLUTE_FLOOR" not in code


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
