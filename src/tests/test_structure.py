from structure import (
    extract_structured_block,
    normalize_line,
    starts_with_verb,
    is_bad_line,
    is_meaningful_line,
    simple_similarity,
)


# ── extract_structured_block ────────────────────────────────────────────

def test_extracts_checklist_from_verb_led_lines():
    checklist = (
        "Check the power connection is stable\n"
        "Verify the network cable is connected\n"
        "Confirm the hub IP is reachable\n"
        "Test the MyConnect app on tablet\n"
        "Ensure device registration completed"
    )
    result = extract_structured_block([{"text": checklist, "source": "manual.pdf"}])

    assert result is not None
    assert "Check the power connection is stable" in result
    assert "Verify the network cable is connected" in result


def test_returns_none_for_prose_paragraph():
    prose = (
        "MyConnect is a system designed to help installers manage device "
        "connectivity across multiple sites efficiently.\n"
        "It provides a centralized dashboard for monitoring hub status and "
        "device health in real time.\n"
        "The system integrates with MyCheckr to automate age verification "
        "workflows for retail environments.\n"
        "Administrators can configure network settings, view logs, and "
        "manage user permissions from a single interface."
    )
    result = extract_structured_block([{"text": prose, "source": "manual.pdf"}])
    assert result is None


def test_returns_none_for_too_few_lines():
    short = {"text": "Check power.\nVerify network."}
    assert extract_structured_block([short]) is None


def test_returns_none_for_empty_chunks():
    assert extract_structured_block([]) is None


# ── normalize_line ─────────────────────────────────────────────────────

def test_normalize_strips_numbering_and_punctuation():
    assert normalize_line("1. Check the power supply.") == "check the power supply"
    assert normalize_line("2) Verify connection!") == "verify connection"


# ── starts_with_verb ──────────────────────────────────────────────────

def test_starts_with_verb_common_verbs():
    assert starts_with_verb("Check the connection") is True
    assert starts_with_verb("Verify network status") is True
    assert starts_with_verb("Connect the tablet") is True


def test_starts_with_verb_short_non_sentence():
    # <=6 words, not ending in "." → treated as imperative/heading
    assert starts_with_verb("Network status indicator") is True


def test_starts_with_verb_long_prose_sentence():
    sentence = "MyConnect is a system designed to help installers manage devices."
    assert starts_with_verb(sentence) is False


# ── is_bad_line ────────────────────────────────────────────────────────

def test_is_bad_line_filters_headers():
    assert is_bad_line("Introduction") is True
    assert is_bad_line("Table of Contents") is True
    assert is_bad_line("1. Overview") is True


def test_is_bad_line_filters_short_lines():
    assert is_bad_line("Check power") is True   # 2 words


def test_is_bad_line_allows_normal_lines():
    assert is_bad_line("Check the power connection is stable") is False


# ── Bug: PDF running-header / section-title artifacts leaking into ────
# extracted checklists. Reproduced directly from production output:
# "MyConnect Environment – 9" (a repeated page header) and "MyConnect
# Environment Installation - Advance Pre-requisites" (a section title)
# both survived every prior is_bad_line filter (short, no terminal-
# punctuation violation, not lowercase-led) and were then marked
# "meaningful" downstream purely because they contain the product name
# — putting page headers at the top of answers for two unrelated
# queries ("checklist before leaving site" and "connect tablet to hub").

def test_is_bad_line_filters_pdf_running_header():
    assert is_bad_line("MyConnect Environment – 9") is True
    assert is_bad_line("MyConnect Environment – 17") is True
    assert is_bad_line("MyConnect Environment – 18") is True


def test_is_bad_line_filters_title_case_section_heading():
    assert is_bad_line("MyConnect Environment Installation - Advance Pre-requisites") is True


def test_is_bad_line_title_case_filter_does_not_catch_real_checklist_items():
    # These are short and/or capitalized-looking PDF extraction output
    # but are genuine, verb-led checklist content — must NOT be filtered.
    assert is_bad_line("Confirm that a dedicated Ethernet cable is available for the MyConnect Hub at the installation site.") is False
    assert is_bad_line("Confirm firewall rules allow communication between MyCheckr, the Hub, and the MyConnect App.") is False
    assert is_bad_line("Check if an electrician is required to assist with relay wiring.") is False


def test_extract_structured_block_excludes_header_artifacts_end_to_end():
    # Full reconstruction of the actual production chunk from the
    # transcript — confirms the header/title lines don't just fail
    # is_bad_line in isolation, but are actually absent from the final
    # extracted answer.
    chunk_text = (
        "MyConnect Environment – 9\n"
        "MyConnect Environment Installation - Advance Pre-requisites\n"
        "Confirm that a dedicated Ethernet cable is available for the MyConnect Hub at the installation site.\n"
        "Hub connects via Ethernet only and requires access to power.\n"
        "Confirm firewall rules allow communication between MyCheckr, the Hub, and the MyConnect App.\n"
        "Verify if modifications are required to integrate MyCheckr with the existing system.\n"
    )
    result = extract_structured_block(
        [{"text": chunk_text, "source": "MyConnect Environment-v4.pdf", "rerank_score": 0.7}],
        query="give me the checklist before leaving site after installation",
    )
    assert result is not None
    assert "MyConnect Environment – 9" not in result
    assert "Advance Pre-requisites" not in result
    assert "Confirm that a dedicated Ethernet cable" in result


# ── is_meaningful_line ────────────────────────────────────────────────

def test_is_meaningful_line():
    assert is_meaningful_line("Verify the network is connected") is True
    assert is_meaningful_line("Check power status") is True
    assert is_meaningful_line("Random unrelated sentence here") is False


# ── simple_similarity ────────────────────────────────────────────────

def test_simple_similarity_identical():
    assert simple_similarity("check the power", "check the power") == 1.0


def test_simple_similarity_disjoint():
    assert simple_similarity("check the power", "verify the network") < 0.5


def test_simple_similarity_empty():
    assert simple_similarity("", "anything") == 0
    assert simple_similarity("anything", "") == 0
