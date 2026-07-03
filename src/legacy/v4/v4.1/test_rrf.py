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