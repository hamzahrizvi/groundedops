"""The "here are some things I can answer" list must be about the right
product, and in a sensible order.

Observed twice in production: logs.jsonl:1969, where a visitor asking about
RMS on a coin hopper was offered three MyCheckr questions, and again on
2026-09-17 13:57, where a visitor four turns into an NV9 conversation got
the same three. In both the turn was UNSCOPED, and list_for_product returns
the whole file when there is no scope -- so the filter that was supposed to
prevent this was never engaged.

Questions here are new ones, not drawn from the eval cases or the logs, and
they are written the way the two kinds of customer actually write: an
installer naming parts and interfaces, and someone who has never seen the
machine describing what they want it to do.
"""
import _harness  # noqa: F401
import main
import faq_store


# A stand-in catalogue: two products, a category entry, and questions in the
# register a customer would use rather than a manual's heading.
ENTRIES = [
    {"id": "1", "question": "What is MyCheckr used for?",
     "answer": "Age verification.", "products": "mycheckr"},
    {"id": "2", "question": "Does MyCheckr need an internet connection?",
     "answer": "No.", "products": "mycheckr"},
    {"id": "3", "question": "What retailing strategy does MyCheckr automate?",
     "answer": "Age-restricted sales.", "products": "mycheckr"},
    {"id": "4", "question": "What note denominations does the NV9 accept?",
     "answer": "All current UK notes.", "products": "nv9_spectral"},
    {"id": "5", "question": "How is the NV9 powered?",
     "answer": "12 V DC.", "products": "nv9_spectral"},
    {"id": "6", "question": "How often should the NV9 note path be cleaned?",
     "answer": "Monthly.", "products": "nv9_spectral"},
]


def _patch_store(monkey_scope=None):
    """list_for_product with the real filtering semantics, over ENTRIES."""
    def fake(scope_key, display_only=False):
        if not scope_key or scope_key == "all":
            return list(ENTRIES)
        return [e for e in ENTRIES if e["products"] == scope_key]
    faq_store.list_for_product = fake


def setup_module(_=None):
    _patch_store()


def test_a_scoped_refusal_only_offers_that_products_questions():
    setup_module()
    out = main._refusal_suggestions(
        "nv9_spectral",
        query="can it read the new polymer twenty in either orientation")
    assert out, "a scoped refusal should still offer something"
    assert all("NV9" in q for q in out), out


def test_an_unscoped_refusal_follows_the_documents_that_were_cited():
    """The 1969 case. No product selected, but retrieval cited the NV9
    manual -- so the suggestions must come from the NV9, not from whichever
    product happens to sit first in the file."""
    setup_module()
    out = main._refusal_suggestions(
        None,
        query="what sort of state do the notes have to be in before it stops taking them",
        sources=[{"source": "NV9 Spectral Range User Manual-v1.pdf"}])
    assert out
    assert not any("MyCheckr" in q for q in out), \
        f"cited the NV9 manual and offered MyCheckr questions: {out}"


def test_an_unscoped_refusal_with_nothing_cited_still_ranks():
    """Worst case -- no scope, nothing retrieved. The list cannot be
    filtered, so ranking is the only thing left. A cleaning question should
    surface the cleaning entry rather than the file's first row."""
    setup_module()
    out = main._refusal_suggestions(
        None,
        query="how do I clean the note path on the NV9 without taking it apart")
    assert out
    assert out[0] == "How often should the NV9 note path be cleaned?", out


def test_ranking_prefers_the_related_question_over_file_order():
    """A shop owner's wording, not a manual's."""
    setup_module()
    out = main._refusal_suggestions(
        None, query="what happens to MyCheckr if the internet drops out")
    assert out[0] == "Does MyCheckr need an internet connection?", out


def test_a_pure_paraphrase_is_not_reordered_and_that_is_accepted():
    """The known limit of lexical ranking, pinned so it is a decision rather
    than a surprise: "offline" and "no wifi" share no words with "internet
    connection", so this question does not promote the entry that answers
    it.

    Semantic ranking would catch it, and is deliberately not used here --
    faq_store._semantic_scores embeds against the WHOLE store regardless of
    the pool passed in, measured at 10.7s cold on 2026-09-19. That is not a
    cost to add to a refusal. Scope filtering is what protects this case:
    the visitor still gets MyCheckr questions, not NV9 ones.
    """
    setup_module()
    out = main._refusal_suggestions(
        "mycheckr", query="does MyCheckr work offline in a shop with no wifi")
    assert len(out) == 3
    assert all("MyCheckr" in q for q in out), out


def test_a_question_sharing_nothing_still_gets_a_list():
    """A refusal has by definition already failed the FAQ matcher's 0.70
    floor, so a hard floor here would empty the list on nearly every
    refusal. Unrelated-but-real suggestions are the point: this is "what the
    documentation covers", not "what matches you"."""
    setup_module()
    out = main._refusal_suggestions(
        "mycheckr", query="do you ship to the Republic of Ireland")
    assert len(out) == 3, out


def test_no_query_keeps_the_old_behaviour():
    """Callers that have no question to rank against still get the scoped
    list rather than nothing."""
    setup_module()
    out = main._refusal_suggestions("mycheckr", query="")
    assert len(out) == 3
    assert all("MyCheckr" in q for q in out)


def test_the_refusal_text_carries_the_ranked_list():
    """End of the path: _friendly_refusal renders what it is given."""
    setup_module()
    text = main._friendly_refusal(
        None, "",
        query="how do I clean the note path on the NV9 without taking it apart",
        sources=[{"source": "NV9 Spectral Range User Manual-v1.pdf"}])
    assert "Here are some things I can answer:" in text
    assert "MyCheckr" not in text, text
    # The internal refusal contract is untouched -- is_refusal must still
    # recognise this, or offer_support and the gap log stop working. Loaded
    # from source: the harness stubs text_utils.is_refusal to False, so
    # importing it here would assert against the stub.
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "_real_text_utils",
        pathlib.Path(__file__).resolve().parent.parent / "text_utils.py")
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    assert real.is_refusal(text), text


def test_a_broken_faq_store_does_not_break_the_refusal():
    def boom(*a, **k):
        raise RuntimeError("store unavailable")
    saved = faq_store.list_for_product
    faq_store.list_for_product = boom
    try:
        text = main._friendly_refusal("nv9_spectral", "NV9 Spectral",
                                      query="is it noisy", sources=[])
        assert "Our support team can help" in text
    finally:
        faq_store.list_for_product = saved


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
