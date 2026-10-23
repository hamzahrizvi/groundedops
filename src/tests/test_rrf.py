from text_utils import rrf_merge


def test_empty_rankings():
    assert rrf_merge() == {}
    assert rrf_merge([], []) == {}


def test_rank_order_within_single_ranking_preserved():
    ranking = ["first", "second", "third"]
    scores = rrf_merge(ranking)
    assert scores["first"] > scores["second"] > scores["third"]


def test_item_in_multiple_rankings_scores_higher():
    bm25  = ["A", "B", "C"]
    dense = ["A", "C", "B"]
    scores = rrf_merge(bm25, dense)
    # A is rank 0 in both lists — should score highest
    assert scores["A"] > scores["B"]
    assert scores["A"] > scores["C"]


def test_bm25_only_item_is_included_even_if_absent_from_dense():
    """
    This is the actual fix in retrieval_db.py: previously BM25 was run
    ONLY on the dense top-10, so a chunk entirely absent from dense
    results (e.g. an out-of-vocabulary domain term like "MyCheckr",
    which all-MiniLM-L6-v2 has never seen) could never surface at all.

    Now BM25 runs independently over the full corpus. A chunk that
    ranks #1 on BM25 but doesn't appear in dense's results at all is
    still included in the merged candidate set with a non-zero score —
    something the old "rerank dense top-10 by BM25" approach could
    never produce.
    """
    bm25  = ["mycheckr_chunk", "other1", "other2"]
    dense = ["other1", "other2", "other3"]   # mycheckr_chunk absent entirely

    scores = rrf_merge(bm25, dense)

    assert "mycheckr_chunk" in scores
    assert scores["mycheckr_chunk"] > 0


def test_k_parameter_dampens_rank_differences():
    ranking = ["a", "b"]
    scores_low_k  = rrf_merge(ranking, k=1)
    scores_high_k = rrf_merge(ranking, k=1000)

    # With small k, rank 0 vs rank 1 differ proportionally more
    ratio_low  = scores_low_k["a"]  / scores_low_k["b"]
    ratio_high = scores_high_k["a"] / scores_high_k["b"]
    assert ratio_low > ratio_high


# ── The candidate window (2026-08-28 fix, pinned 2026-09-22) ─────────────
#
# rrf_merge including a single-list chunk with a non-zero score, as the test
# above asserts, is NOT the same as that chunk surviving to the reranker.
# With RRF_K=60 a chunk only one arm found caps at 1/61 = 0.0164, while any
# chunk both arms rank in their top ten scores ~0.028+ -- so a dense #1 that
# BM25 never saw loses to chunks both arms rank mediocrely, and used to be
# cut before the one stage that would have recognised it.
#
# These pin apply_arm_guarantee, which is the fix. They are here rather than
# in a retrieval integration test because the failure is pure list algebra;
# the history is that the candidate margin got silently dropped twice by
# edits elsewhere in retrieve_from_db, which a unit test catches and an
# end-to-end suite (+/-3 cases of generation noise) does not.

from retrieval_db import apply_arm_guarantee


def test_arm_guarantee_rescues_a_chunk_rrf_buried():
    # Dense is certain; BM25 has never heard of it.
    dense = ["buried", "d1", "d2"]
    bm25  = ["b0", "b1", "b2"]
    # RRF put "buried" outside the cut because it appears in one list only.
    ranked = ["b0", "d1", "b1", "d2", "b2", "buried"]

    kept = apply_arm_guarantee(ranked, 4, dense, bm25, guarantee=3)

    assert "buried" in kept
    # Rescued, not promoted: the RRF head keeps its order and its places.
    assert kept[:4] == ranked[:4]


def test_arm_guarantee_zero_is_the_old_behaviour():
    dense = ["buried", "d1"]
    ranked = ["b0", "d1", "b1", "d2", "buried"]
    assert apply_arm_guarantee(ranked, 3, dense, guarantee=0) == ["b0", "d1", "b1"]


def test_arm_guarantee_admits_only_a_few_extras():
    """The rejected fix was widening the window to top_k*2, which cost two
    cases and dropped mean grounding 0.993 -> 0.954 because 16 extra
    mediocre candidates displace good ones at CONTEXT_K. The guarantee must
    stay cheap: at most `guarantee` per arm, and normally far fewer because
    consensus chunks are already in the head."""
    dense = ["A", "B", "C"]
    bm25  = ["A", "B", "D"]
    ranked = ["A", "B", "X", "Y", "C", "D"]

    kept = apply_arm_guarantee(ranked, 2, dense, bm25, guarantee=3)

    # A and B were already in the head; only C and D are new. Not 2x the window.
    assert kept == ["A", "B", "C", "D"]


def test_arm_guarantee_does_not_duplicate():
    dense = ["A", "B"]
    bm25  = ["B", "A"]
    kept = apply_arm_guarantee(["A", "Z"], 2, dense, bm25, guarantee=2)
    assert kept == ["A", "Z", "B"]
    assert len(kept) == len(set(kept))


def test_nv9s_weight_case_survives_the_cut():
    """Regression, from the real failure. "How much does the NV9S validator
    weigh on its own?" -- the correct chunk (p25, "Validator NV9S: 1.05 Kg")
    was dense #1, absent from BM25 ("weigh" does not lexically match
    "Weights"), and landed at RRF rank 20 of 46 against a cut of 16. Handed
    to the reranker it scores 0.9928; the table-of-contents chunk that won
    instead scores 0.2165.

    Reconstructed with the measured arm ranks of the five chunks that beat
    it -- (bm25, dense) = (7,6), (8,7), (9,10), (19,3), (2,22) -- all
    mediocre in both arms, all winning purely on appearing twice."""
    correct = "nv9_spectral_25"
    dense = [correct] + [f"consensus_{i}" for i in range(15)]
    bm25  = [f"consensus_{i}" for i in range(15)] + ["toc_chunk"]
    # What RRF produces: consensus chunks first, the single-arm #1 at rank 20.
    ranked = [f"consensus_{i}" for i in range(15)] + ["toc_chunk"] + \
             [f"tail_{i}" for i in range(3)] + [correct]

    top_k = 16
    assert correct not in ranked[:top_k], "precondition: RRF buries it"

    kept = apply_arm_guarantee(ranked, top_k, dense, bm25, guarantee=3)

    assert correct in kept, "the reranker never gets to see the right answer"


def test_arm_guarantee_is_on_by_default():
    """The algebra above is only reached if the knob is non-zero. The
    default was chosen on measurement (12/51 queries rescued a candidate,
    1 answer changed, 0 regressions, grounding and latency flat); a revert
    to 0 restores the refusal, so it is asserted rather than assumed."""
    import retrieval_db
    assert retrieval_db.RETRIEVAL_ARM_GUARANTEE > 0
    # And the rejected fix stays rejected: widening the window instead cost
    # two cases and dropped mean grounding 0.9934 -> 0.9539.
    assert retrieval_db.RETRIEVAL_CANDIDATE_MARGIN == 1.0


# ── BM25 tokenisation ────────────────────────────────────────────────
#
# The query and the corpus must be tokenised the SAME way. For years both
# used a bare `text.lower().split()`, so a token kept whatever punctuation
# touched it and the two sides quietly disagreed about every word that
# ended a sentence.
from retrieval_db import _bm25_tokens


def test_trailing_question_mark_does_not_eat_the_term():
    """THE BUG. "Can I connect to MyCheckr using linux?" asked BM25 for
    the term `linux?`, which is in no document, so it scored 0.0000 and
    contributed nothing -- measured against the real index:

        get_scores(["linux"])   max 9.5688   top 3 = the Linux document
        get_scores(["linux?"])  max 0.0000   not in the idf table at all

    The question silently became "can i connect to mycheckr using" and
    returned MyCheckr manuals. This was never about Linux: the last word
    of a question collects the "?" and is very often the most specific
    term in it -- idf("linux") is 5.018 against idf("mycheckr") 2.233.
    """
    assert _bm25_tokens("Can I connect to MyCheckr using linux?")[-1] == "linux"
    assert _bm25_tokens("does it support ccTalk?")[-1] == "cctalk"
    assert _bm25_tokens("what is the weight of the NV200S?")[-1] == "nv200s"


def test_the_corpus_side_is_tokenised_the_same_way():
    """"environment:" and "environment" were two different terms, so a
    corpus word followed by punctuation was as unreachable as a query
    word followed by one."""
    assert "environment" in _bm25_tokens("in a Linux-based environment:")
    assert _bm25_tokens("the host machine (PC).") == ["the", "host",
                                                      "machine", "pc"]


def test_internal_structure_is_preserved():
    """Stripping to \w+ would break all three of these, and each one
    matters in this corpus: NV9USB+ and NV9USB are different products, an
    IP address is one term, and "linux-based" is not two words."""
    assert _bm25_tokens("the NV9USB+ range") == ["the", "nv9usb+", "range"]
    assert _bm25_tokens("static at 192.168.137.8.")[-1] == "192.168.137.8"
    assert _bm25_tokens("a linux-based host")[1] == "linux-based"


def test_a_leading_plus_survives():
    """"+ 24 V DC" is how the voltage tables are written."""
    assert _bm25_tokens("Nominal + 24 V DC") == ["nominal", "+", "24", "v", "dc"]


def test_punctuation_only_tokens_are_dropped():
    assert _bm25_tokens("what -- exactly -- is this?") == [
        "what", "exactly", "is", "this"]
    assert _bm25_tokens("") == []
