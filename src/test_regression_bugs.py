"""
Regression tests for bugs found via direct production transcript analysis
(see main conversation history for the full repro/verification process).
Each test is built from the ACTUAL queries/content that triggered the bug.
"""

from chunking import chunk_text
from structure import extract_structured_block
from text_utils import (
    fix_camel_case,
    clean_table_artifacts,
    build_retrieval_query,
    retrieval_confidence_band,
    looks_like_followup,
)


# ── Bug: "MyConnect" / "MyCheckr" / "WiFi" mangled by camelCase fix ──────
# Transcript showed the assistant repeatedly saying "My Connect App" and
# "My Checkr" instead of the correct product names.

def test_camelcase_preserves_myconnect():
    assert fix_camel_case("Open MyConnect App") == "Open MyConnect App"


def test_camelcase_preserves_mycheckr():
    assert fix_camel_case("Register the MyCheckr") == "Register the MyCheckr"


def test_camelcase_preserves_wifi():
    assert fix_camel_case("Connect to the WiFi network") == "Connect to the WiFi network"


def test_camelcase_still_fixes_genuine_merge_artifacts():
    # The original purpose of the regex must still work for terms NOT
    # on the protected list.
    assert fix_camel_case("activityCategory") == "activity Category"
    assert fix_camel_case("deviceStatusReady") == "device Status Ready"


# ── Bug: irrelevant-but-longer chunk beats relevant-but-shorter chunk ───
# Reproduces the exact production failure: "how to connect hub to
# MyConnect app?" repeatedly returned GPIO/relay installer notes instead
# of the actual Step 4 (connect tablet/app) content, because the old
# scoring summed structural points with no length normalization and
# barely weighted rerank_score or query overlap.

def test_relevant_chunk_wins_over_longer_irrelevant_chunk():
    relay_notes = (
        "Select action type (eg. Hub Relay)\n"
        "Select group that would trigger that action\n"
        "Overage Relay 1 Unlock Door\n"
        "Underage Relay 2 Flash Beacon\n"
        "Step 8 Test the System\n"
        "Approach the MyCheckr with a person under or over age\n"
        "Confirm alerts appear on tablet\n"
        "Confirm relay activates door or light\n"
        "Make sure GPIO Duration equals 1 second from Hub settings"
    )
    connect_steps = (
        "Step 4 — Connect the Tablet & Open MyConnect App\n"
        "Step 5 — Add and Register the MyCheckr\n"
        "Step 6 — Configure Device Rules\n"
        "Step 7 — Assign Relay Actions (Hub Outputs)\n"
        "Step 8 — Test the System"
    )

    chunks = [
        # Lower rerank score AND zero query overlap, but more lines —
        # this used to win purely on volume.
        {"text": relay_notes, "source": "manual.pdf", "rerank_score": 0.50},
        # Higher rerank score (genuinely relevant), fewer lines.
        {"text": connect_steps, "source": "manual.pdf", "rerank_score": 0.85},
    ]

    result = extract_structured_block(chunks, query="how to connect hub to MyConnect app?")

    assert result is not None
    assert "Step 4" in result
    assert "Connect the Tablet" in result


def test_rerank_score_breaks_ties_when_structure_is_similar():
    # Two equally list-shaped chunks, query only matches one — the
    # matching one (and the one with the higher rerank_score) should win.
    chunk_a = {
        "text": "Check the battery level\nVerify the casing is sealed\nConfirm firmware version is current",
        "source": "a.pdf", "rerank_score": 0.30,
    }
    chunk_b = {
        "text": "Check the hub power light\nVerify the hub WiFi connection\nConfirm the hub IP is reachable",
        "source": "b.pdf", "rerank_score": 0.80,
    }
    result = extract_structured_block([chunk_a, chunk_b], query="how is the hub connected to wifi")
    assert result is not None
    assert "hub" in result.lower()


# ── Bug: long narrative sentence included as a checklist item ──────────
# Transcript: "This final section is a short, single checklist ensuring
# nothing has been forgotten. Installers are expected to tick all items
# before signing off." was returned as if it were a checklist line.

def test_narrative_intro_excluded_from_checklist():
    chunk_text_value = (
        "This final section is a short, single checklist ensuring nothing "
        "has been forgotten before you leave the site for good measure.\n"
        "Check power is connected and stable\n"
        "Verify network connection is active\n"
        "Confirm hub IP is reachable from tablet\n"
        "Ensure all alerts trigger correctly on the tablet screen"
    )
    chunks = [{"text": chunk_text_value, "source": "manual.pdf", "rerank_score": 0.7}]
    result = extract_structured_block(chunks, query="give installer checklist when leaving site")

    assert result is not None
    assert "This final section" not in result
    assert "Check power is connected" in result


# ── Bug: checkbox/table-merge artifacts garbling output ─────────────────

def test_checkbox_glued_to_text_gets_separated():
    cleaned = clean_table_artifacts("activity☐Category Verification Step Check")
    assert "☐" in cleaned
    # The glued word and the checkbox marker must no longer be one token
    assert "activity☐" not in cleaned


def test_dangling_open_paren_stripped():
    cleaned = clean_table_artifacts("Fail-safe NC wiring tested (if")
    assert cleaned == "Fail-safe NC wiring tested"
    assert "(" not in cleaned


def test_balanced_parens_untouched():
    cleaned = clean_table_artifacts("System Features Dual action (API + Relay)")
    assert cleaned == "System Features Dual action (API + Relay)"


# ── Bug: total chunking-merge of an entire multi-step section ──────────
# Reproduces: with no blank lines between Step 4 and Step 8 + Installer
# Notes (a common PDF-extraction artifact), the old chunker glued the
# ENTIRE section into one atomic, indivisible unit no matter how long it
# was, so retrieval/extraction had no way to isolate just one step.

def test_long_multi_step_section_gets_split_into_multiple_chunks():
    doc = "\n".join([
        "Step 1 — Power On the Hub",
        "Connect the power adapter to the Hub.",
        "Wait for the LED to turn solid green.",
        "Step 2 — Connect to WiFi",
        "Open the Hub settings menu on the tablet.",
        "Select the installation WiFi network.",
        "Enter the network password when prompted.",
        "Step 3 — Mount the Hub",
        "Choose a wall location near the entry point.",
        "Use the supplied screws to mount the bracket.",
        "Step 4 — Connect the Tablet & Open MyConnect App",
        "Connect the tablet to the same WiFi network as the Hub.",
        "Download the MyConnect App from the Downloads folder.",
        "Step 5 — Add and Register the MyCheckr",
        "Open the Devices menu inside MyConnect.",
        "Tap Add Device and select MyCheckr.",
        "Step 6 — Configure Device Rules",
        "Set the age threshold for the policy.",
        "Step 7 — Assign Relay Actions",
        "Select action type for the relay.",
        "Step 8 — Test the System",
        "Approach the MyCheckr to trigger a test alert.",
        "Installer Notes",
        "Set GPIO duration from the Hub settings menu.",
        "Always test the relay immediately after wiring.",
    ])

    chunks = chunk_text(doc, size=500, overlap=50)

    # The old chunker produced exactly 1 chunk for content like this.
    assert len(chunks) > 1

    # No single chunk should contain BOTH Step 1 and Step 8 — if it does,
    # the section is still being glued together wholesale.
    for c in chunks:
        assert not ("Step 1" in c and "Step 8" in c)


def test_short_section_with_no_step_boundary_still_chunks_fine():
    # Sanity check: short content with no Step headers at all should
    # still produce a sensible single chunk, not error or vanish.
    doc = "Check power\nVerify network\nConfirm hub IP\nTest tablet alert"
    chunks = chunk_text(doc, size=500, overlap=50)
    assert len(chunks) == 1
    assert "Check power" in chunks[0]


# ── Bug: follow-up query with no standalone signal fails retrieval ──────
# Transcript: "give me that from step 1" returned "I could not find that
# in the knowledge base" — the query alone has no semantic content to
# match against.

def test_followup_query_gets_enriched_with_previous_query():
    enriched = build_retrieval_query("give me that from step 1", "how to connect hub to MyConnect app")
    assert "MyConnect" in enriched
    assert "step 1" in enriched.lower()


def test_standalone_query_unchanged_when_not_a_followup():
    long_query = "what are the default login credentials for the standalone tablet device"
    assert build_retrieval_query(long_query, "some previous query") == long_query


def test_followup_detection_short_queries():
    assert looks_like_followup("give me that from step 1") is True
    assert looks_like_followup("more") is True


def test_followup_detection_self_contained_query():
    assert looks_like_followup(
        "what is the default login credential for the standalone hub device unit"
    ) is False


# ── New feature: ambiguous-confidence clarifying-question path ─────────

def test_confidence_band_none_when_gate_fails():
    results = [{"rerank_score": 0.1, "source": "a.pdf"}]
    assert retrieval_confidence_band(results) == "none"


def test_confidence_band_confident_high_score():
    results = [{"rerank_score": 0.9, "source": "a.pdf"}]
    assert retrieval_confidence_band(results) == "confident"


def test_confidence_band_ambiguous_borderline_score_scattered_sources():
    results = [
        {"rerank_score": 0.55, "source": "install_guide.pdf"},
        {"rerank_score": 0.53, "source": "troubleshooting.pdf"},
        {"rerank_score": 0.52, "source": "api_reference.pdf"},
        {"rerank_score": 0.51, "source": "faq.pdf"},
    ]
    assert retrieval_confidence_band(results) == "ambiguous"


def test_confidence_band_borderline_score_but_single_topic_is_confident():
    # Borderline score but all results agree on the same source/topic —
    # shouldn't interrupt the user unnecessarily.
    results = [
        {"rerank_score": 0.55, "source": "install_guide.pdf"},
        {"rerank_score": 0.53, "source": "install_guide.pdf"},
    ]
    assert retrieval_confidence_band(results) == "confident"