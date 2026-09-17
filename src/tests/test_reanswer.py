"""Re-reading the retrieved passages, and the parsing that makes it safe.

Chosen over swapping the reranker on evidence. tools/bench_reranker.py over
the 19-case retrieval suite, three cross-encoders, identical candidate sets:

    model                         r@1   r@3   r@8    sec/q
    ms-marco-MiniLM-L-6-v2 (now)  68%   94%   100%    2.0
    ms-marco-MiniLM-L-12-v2       63%   94%    94%    6.0
    BAAI/bge-reranker-base        73%   94%   100%   17.1

The relevant chunk reaches the context every time; only its POSITION is
wrong, and the best reranker buys five points of r@1 for 8.5x the latency
on a CPU-only box. r@3 is 94% everywhere, which is what makes both the
selection and the top-3 merge worth doing.
"""
import reanswer


def test_plain_numbers():
    assert reanswer.parse_selection("1, 3", 8) == [0, 2]
    assert reanswer.parse_selection("2", 8) == [1]


def test_forgiving_about_wrapping():
    """A small model asked for "1, 3" will sometimes dress it up."""
    assert reanswer.parse_selection("Passages 1 and 3", 8) == [0, 2]
    assert reanswer.parse_selection("[1][4]", 8) == [0, 3]
    assert reanswer.parse_selection("  2 , 5 \n", 8) == [1, 4]


def test_none_means_none():
    assert reanswer.parse_selection("NONE", 8) == []
    assert reanswer.parse_selection("none", 8) == []
    assert reanswer.parse_selection("", 8) == []


def test_out_of_range_numbers_are_dropped():
    """A hallucinated [9] against 8 candidates would otherwise index into
    nothing, or wrap around to the wrong passage."""
    assert reanswer.parse_selection("9", 8) == []
    assert reanswer.parse_selection("1, 99, 3", 8) == [0, 2]
    assert reanswer.parse_selection("0", 8) == []


def test_duplicates_collapse_and_order_is_kept():
    assert reanswer.parse_selection("3, 1, 3", 8) == [2, 0]


def test_selection_wins_when_present():
    ranked = [{"text": "a"}, {"text": "b"}, {"text": "c"}, {"text": "d"}]
    got, mode = reanswer.chosen_passages([2], ranked)
    assert [c["text"] for c in got] == ["c"]
    assert mode == "selected"


def test_falls_back_to_the_top_three_merged():
    """The fallback is what makes this safe to put behind a button: a
    selector that returns nothing must not produce a worse answer than the
    one the visitor already had."""
    ranked = [{"text": "a"}, {"text": "b"}, {"text": "c"}, {"text": "d"}]
    got, mode = reanswer.chosen_passages([], ranked)
    assert [c["text"] for c in got] == ["a", "b", "c"]
    assert mode == "merged_top_3"


def test_fallback_is_safe_on_a_short_list():
    got, mode = reanswer.chosen_passages([], [{"text": "a"}])
    assert [c["text"] for c in got] == ["a"]


def test_prompt_numbers_the_passages_from_one():
    p = reanswer.build_selection_prompt("does X work with Y?",
                                        ["alpha", "beta", "gamma"])
    assert "[1] alpha" in p and "[2] beta" in p and "[3] gamma" in p
    assert "does X work with Y?" in p


def test_prompt_rejects_mere_mention():
    """The exact failure this exists for: a table listing two devices was
    served as the answer to whether they work together."""
    p = reanswer.build_selection_prompt("q", ["a"])
    assert "does NOT answer the question" in p
    assert "NONE" in p


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
