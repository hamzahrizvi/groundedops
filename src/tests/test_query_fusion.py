"""Retrieval fuses the raw and rewritten phrasings of a question.

The fault being pinned (PENDING "Condensation can destroy a working query"):
condense_query's rewrite REPLACED the question for retrieval, so resolving a
follow-up could lose hits the question had as typed. Measured on "what are
the power requirements for this setup?" -- as typed the PSU page reranked #1
(0.9348); resolved to name both products it was not retrieved at all.

The contract is STRICTLY ADDITIVE, and these tests exist mostly to keep it
that way. Two earlier designs were measured against the live index and both
lost the rewrite's own best chunk: RRF-fusing the two lists and trimming to
top_k, and reserving an equal share of top_k per query. A rewrite drops 9-13
of the 16 candidates the question had as typed, so ANY fixed-size merge
evicts about half of each list, and the reranker's best pick routinely sits
at retrieval rank 9-16. Fusion that can lose the rewrite's hits is the same
bug as a rewrite that loses the original's, pointed the other way.

Pure list algebra, deliberately: the failure is in which candidates survive
a merge, so it is testable without an index, a provider or an embedder --
the same reason tests/test_rrf.py covers the arm guarantee this reuses.

Deliberately does NOT import _harness: the harness stubs text_utils.
"""
from retrieval_db import fuse_ranked_results


def _rs(*ids, score=0.02):
    """A retrieve_from_db-shaped result list, best-first."""
    return [{"id": i, "text": i, "source": "m.pdf", "page": 1,
             "retrieval_score": score} for i in ids]


def test_nothing_the_primary_query_found_is_ever_dropped():
    """THE contract. Both rejected designs failed exactly here."""
    primary = _rs(*[f"p{i}" for i in range(16)])
    secondary = _rs(*[f"s{i}" for i in range(16)])

    fused = fuse_ranked_results([primary, secondary])
    ids = [r["id"] for r in fused]

    for r in primary:
        assert r["id"] in ids, f"{r['id']} was evicted"


def test_a_hit_only_the_raw_phrasing_found_is_rescued():
    """The other half of the point: the rewrite never saw `psu_page`."""
    rewritten = _rs("firmware_page", "r1", "r2", "r3")
    as_typed = _rs("psu_page", "t1", "t2", "t3")

    ids = [r["id"] for r in fuse_ranked_results([rewritten, as_typed])]

    assert "psu_page" in ids
    assert "firmware_page" in ids


def test_the_rescue_stays_small():
    """Widening the window was measured on 2026-08-28 and cost two cases:
    extra mediocre candidates displace good ones when the reranker picks
    CONTEXT_K. So a second phrasing contributes its top few, not its 16 --
    measured at +1.6 candidates per query over the live index."""
    primary = _rs(*[f"p{i}" for i in range(16)])
    secondary = _rs(*[f"s{i}" for i in range(16)])

    fused = fuse_ranked_results([primary, secondary], guarantee=3)

    assert len(fused) == 19, len(fused)


def test_the_primary_phrasing_keeps_its_order():
    """Order is not fused, because nothing downstream reads it: main.py
    reranks the FULL candidate list and only then truncates to CONTEXT_K. An
    ordering pass would compute something unread, and its one real effect --
    deciding who gets trimmed -- is the bug above."""
    primary = _rs("a", "b", "c")
    secondary = _rs("z")

    ids = [r["id"] for r in fuse_ranked_results([primary, secondary])]

    assert ids[:3] == ["a", "b", "c"], ids


def test_overlap_is_not_duplicated():
    primary = _rs("shared", "a")
    secondary = _rs("shared", "b")

    ids = [r["id"] for r in fuse_ranked_results([primary, secondary])]

    assert ids.count("shared") == 1
    assert len(ids) == len(set(ids))


def test_retrieval_score_is_the_best_any_phrasing_reached():
    """Kept on the single-query scale so logged values stay comparable --
    a fused figure would not be. Nothing gates on it (every gate reads
    rerank_score), but it is reported."""
    weak = [{"id": "c", "text": "c", "source": "m.pdf", "page": 1,
             "retrieval_score": 0.0164}]
    strong = [{"id": "c", "text": "c", "source": "m.pdf", "page": 1,
               "retrieval_score": 0.0303}]

    fused = fuse_ranked_results([weak, strong])

    assert len(fused) == 1
    assert fused[0]["retrieval_score"] == 0.0303


def test_one_phrasing_is_the_old_path_untouched():
    """A turn with no rewrite must be bit-for-bit the single-query path and
    must not pay for a second retrieval."""
    only = _rs("a", "b", "c")
    assert fuse_ranked_results([only]) == only
    assert fuse_ranked_results([only, []]) == only


def test_no_results_at_all():
    assert fuse_ranked_results([]) == []
    assert fuse_ranked_results([[], []]) == []


def test_guarantee_zero_is_the_primary_query_alone():
    """The knob that turns fusion off without a deploy: the candidate set
    collapses to exactly what the rewritten query retrieved."""
    primary = _rs("a", "b")
    secondary = _rs("z")

    ids = [r["id"] for r in fuse_ranked_results([primary, secondary],
                                                guarantee=0)]

    assert ids == ["a", "b"]
