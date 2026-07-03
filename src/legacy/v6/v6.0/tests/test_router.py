"""
Tests for router.py's semantic routing.

Split into two tiers, matching the project's established pattern for ML-
adjacent code (see llm.condense_query / tests/test_regression_bugs.py's
condensation tests):

  1. PURE LOGIC (text_utils.classify_by_similarity) — numpy-only, no
     sentence-transformers required, runs unconditionally in any
     environment including this sandbox. This is what actually proves
     the classification MATH is correct.

  2. INTEGRATION (router.route_model) — requires sentence-transformers
     to load the real embedding model and produce real vectors for the
     canonical examples. Skipped with a clear message (not silently
     passed, not crashed) when that dependency isn't installed. This is
     the documented verification boundary: the routing LOGIC is proven
     correct by tier 1, but whether real query embeddings actually land
     close to the right canonical examples can only be confirmed with
     sentence-transformers installed.
"""

import importlib.util

import numpy as np

from text_utils import classify_by_similarity

SENTENCE_TRANSFORMERS_AVAILABLE = importlib.util.find_spec("sentence_transformers") is not None


def _norm(v):
    v = np.array(v, dtype=float)
    return v / np.linalg.norm(v)


# ── Tier 1: pure classification logic ───────────────────────────────────

def test_classify_picks_closest_category():
    categories = {
        "reasoning": [_norm([1, 0])],
        "extract": [_norm([0, 1])],
    }
    query = _norm([0.95, 0.05])
    assert classify_by_similarity(query, categories) == "reasoning"


def test_classify_uses_best_match_not_average():
    # "extract" has one very close example and one far one; "reasoning"
    # has two moderately-close examples. The query should still match
    # "extract" via its single best example, not lose out to averaging.
    categories = {
        "extract": [_norm([1, 0]), _norm([-1, 0.01])],
        "reasoning": [_norm([0.7, 0.3]), _norm([0.6, 0.4])],
    }
    query = _norm([0.99, 0.01])
    assert classify_by_similarity(query, categories) == "extract"


def test_classify_falls_back_to_default_when_nothing_close():
    categories = {
        "reasoning": [_norm([1, 0])],
        "extract": [_norm([0, 1])],
    }
    query = _norm([0, -1])
    assert classify_by_similarity(query, categories, min_confidence=0.3, default="accurate") == "accurate"


def test_classify_empty_categories_returns_default():
    query = _norm([1, 0])
    assert classify_by_similarity(query, {}, default="accurate") == "accurate"


def test_classify_category_with_no_vectors_is_skipped_not_crashed():
    categories = {"empty_role": [], "reasoning": [_norm([1, 0])]}
    query = _norm([0.99, 0.01])
    assert classify_by_similarity(query, categories) == "reasoning"


def test_classify_confidence_threshold_is_respected():
    categories = {"reasoning": [_norm([1, 0])]}
    query = _norm([0.5, 0.5])   # similarity ~0.707 to [1,0]
    # High threshold should reject this match and fall back
    assert classify_by_similarity(query, categories, min_confidence=0.9, default="accurate") == "accurate"
    # Low threshold should accept it
    assert classify_by_similarity(query, categories, min_confidence=0.5, default="accurate") == "reasoning"


# ── Tier 2: integration (requires sentence-transformers) ───────────────

def _skip_if_no_sentence_transformers():
    if not SENTENCE_TRANSFORMERS_AVAILABLE:
        reason = "sentence-transformers not installed in this environment"
        try:
            import pytest
            pytest.skip(reason)
        except ImportError:
            # Running under run_tests.py (no pytest) — its SkipTest marker
            # is injected into every test module's globals before exec.
            raise SkipTest(reason)


def test_route_model_extract_for_checklist_query():
    _skip_if_no_sentence_transformers()
    from router import route_model
    role, _ = route_model("give me the checklist before leaving site after installation")
    assert role == "extract"


def test_route_model_extract_for_paraphrased_howto():
    _skip_if_no_sentence_transformers()
    from router import route_model
    # Deliberately NOT matching any old keyword list verbatim — this is
    # the exact class of query the keyword-list router used to miss.
    role, _ = route_model("can you walk me through getting the tablet talking to the hub")
    assert role == "extract"


def test_route_model_reasoning_for_paraphrased_why():
    _skip_if_no_sentence_transformers()
    from router import route_model
    role, _ = route_model("what's the reason device registration sometimes doesn't work")
    assert role == "reasoning"


def test_route_model_fast_for_simple_lookup():
    _skip_if_no_sentence_transformers()
    from router import route_model
    role, _ = route_model("what is the hub ip")
    assert role == "fast"


def test_route_model_out_of_domain_defaults_to_accurate():
    _skip_if_no_sentence_transformers()
    from router import route_model
    role, _ = route_model("what is the capital of france")
    assert role == "accurate"
