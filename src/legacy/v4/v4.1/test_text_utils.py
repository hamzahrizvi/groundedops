"""
Tests for text_utils — pure logic, no ML deps. Covers the exact failure
modes seen in test_queries.py runs (queries 9, 11, 12).
"""

from text_utils import split_units, truncate_after_refusal, passes_retrieval_gate


# ── split_units ────────────────────────────────────────────────────────────

def test_split_units_prose():
    answer = "MyConnect works with MyCheckr to automate age checks. The hub coordinates devices on the network."
    units = split_units(answer)
    assert len(units) == 2
    assert "MyConnect works with MyCheckr" in units[0]


def test_split_units_numbered_list():
    # This is query 12's answer shape — previously scored 0.014 grounded
    # because ". "-splitting produced fragments like "1" with no content.
    answer = (
        "1. Connect the Tablet & Open MyConnect App\n"
        "2. Add and Register the MyCheckr\n"
        "3. Configure Device Rules\n"
        "4. Verify the connection is active"
    )
    units = split_units(answer)
    assert len(units) == 4
    assert units[0] == "Connect the Tablet & Open MyConnect App"
    # No leading numeric markers should survive
    assert not any(u[0].isdigit() for u in units)


def test_split_units_bulleted_list():
    answer = "- Check power\n- Verify network connection\n- Confirm hub IP is reachable"
    units = split_units(answer)
    assert len(units) == 3
    assert units[0] == "Check power"


def test_split_units_short_fragments_dropped():
    # Fragments shorter than MIN_UNIT_LEN (12 chars) should be dropped
    answer = "OK.\nYes.\nThis is a sufficiently long sentence to keep."
    units = split_units(answer)
    assert len(units) == 1
    assert "sufficiently long" in units[0]


def test_split_units_empty_input():
    assert split_units("") == []
    assert split_units("   \n  ") == []


# ── truncate_after_refusal ───────────────────────────────────────────────

def test_truncate_canonical_refusal_with_ramble():
    text = (
        'I could not find that in the knowledge base. '
        'In a hypothetical scenario, you are an IoT engineer working on '
        'a project to automate age verification...'
    )
    result = truncate_after_refusal(text)
    assert result == "I could not find that in the knowledge base."
    assert "hypothetical" not in result


def test_truncate_variant_phrasing_query9():
    # Actual phi output for "what is the capital of france"
    text = (
        "I'm sorry, but there is no information about the capital of "
        "France inside the given context. In a hypothetical scenario, "
        "you are an IoT engineer working on a project to automate the "
        "age verification process using MyChe..."
    )
    result = truncate_after_refusal(text)
    assert "hypothetical" not in result
    assert "there is no information about" in result.lower()


def test_truncate_variant_phrasing_query11():
    # Actual phi output for "introduction of myconnect system"
    text = (
        "I'm sorry, but there is no information about the introduction "
        "of MyConnect System inside the given context. The first step "
        "to solve this puzzle involves using inductive logic..."
    )
    result = truncate_after_refusal(text)
    assert "puzzle" not in result
    assert "inductive logic" not in result


def test_truncate_no_refusal_leaves_text_unchanged():
    text = "The default login credentials for MyConnect are user1 / password1."
    assert truncate_after_refusal(text) == text


def test_truncate_picks_earliest_match():
    # If multiple refusal-like phrases appear, cut at the FIRST one
    text = (
        "The context does not contain this information. "
        "Also, there is no information about this in the docs either. "
        "Extra trailing content."
    )
    result = truncate_after_refusal(text)
    assert "Extra trailing content" not in result
    assert result.startswith("The context does not contain this information")


# ── passes_retrieval_gate ────────────────────────────────────────────────

def test_retrieval_gate_passes_relevant_chunk():
    results = [{"text": "...", "rerank_score": 0.92}]
    assert passes_retrieval_gate(results, threshold=0.5) is True


def test_retrieval_gate_rejects_irrelevant_chunk():
    # Out-of-domain query (e.g. "capital of france") against a
    # MyConnect/MyCheckr corpus — top chunk should score below the
    # sigmoid midpoint (0.5).
    results = [{"text": "...", "rerank_score": 0.08}]
    assert passes_retrieval_gate(results, threshold=0.5) is False


def test_retrieval_gate_empty_results():
    assert passes_retrieval_gate([], threshold=0.5) is False


def test_retrieval_gate_missing_score_defaults_to_zero():
    results = [{"text": "..."}]   # no rerank_score key
    assert passes_retrieval_gate(results, threshold=0.5) is False