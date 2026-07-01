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
    retrieval_confidence_band,
    build_condense_prompt,
    parse_condense_output,
)


# ── Bug: "MyConnect" / "MyCheckr" / "WiFi" mangled by camelCase fix ──────

def test_camelcase_preserves_myconnect():
    assert fix_camel_case("Open MyConnect App") == "Open MyConnect App"


def test_camelcase_preserves_mycheckr():
    assert fix_camel_case("Register the MyCheckr") == "Register the MyCheckr"


def test_camelcase_preserves_wifi():
    assert fix_camel_case("Connect to the WiFi network") == "Connect to the WiFi network"


def test_camelcase_still_fixes_genuine_merge_artifacts():
    assert fix_camel_case("activityCategory") == "activity Category"
    assert fix_camel_case("deviceStatusReady") == "device Status Ready"


# ── Bug: irrelevant-but-longer chunk beats relevant-but-shorter chunk ───

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
        {"text": relay_notes, "source": "manual.pdf", "rerank_score": 0.50},
        {"text": connect_steps, "source": "manual.pdf", "rerank_score": 0.85},
    ]

    result = extract_structured_block(chunks, query="how to connect hub to MyConnect app?")

    assert result is not None
    assert "Step 4" in result
    assert "Connect the Tablet" in result


def test_rerank_score_breaks_ties_when_structure_is_similar():
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
    assert "activity☐" not in cleaned


def test_dangling_open_paren_stripped():
    cleaned = clean_table_artifacts("Fail-safe NC wiring tested (if")
    assert cleaned == "Fail-safe NC wiring tested"
    assert "(" not in cleaned


def test_balanced_parens_untouched():
    cleaned = clean_table_artifacts("System Features Dual action (API + Relay)")
    assert cleaned == "System Features Dual action (API + Relay)"


# ── Bug: total chunking-merge of an entire multi-step section ──────────

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

    assert len(chunks) > 1
    for c in chunks:
        assert not ("Step 1" in c and "Step 8" in c)


def test_short_section_with_no_step_boundary_still_chunks_fine():
    doc = "Check power\nVerify network\nConfirm hub IP\nTest tablet alert"
    chunks = chunk_text(doc, size=500, overlap=50)
    assert len(chunks) == 1
    assert "Check power" in chunks[0]


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
    results = [
        {"rerank_score": 0.55, "source": "install_guide.pdf"},
        {"rerank_score": 0.53, "source": "install_guide.pdf"},
    ]
    assert retrieval_confidence_band(results) == "confident"


# ── Bug: word-count follow-up heuristic flagged complete questions ─────
# Transcript: "how to connect tablet to hub" (6 words), "give me the
# checklist before leaving site after installation" (9 words), and
# "give me steps to install and verify system is working" (10 words)
# were ALL flagged as follow-ups by the old "<=10 words" heuristic, even
# though every one is a complete, self-contained question. Each then got
# silently concatenated with an unrelated previous query before
# retrieval ran. REPLACED by LLM-based query condensation (see
# llm.condense_query and the "conversational query condensation" section
# of text_utils.py) — these tests cover the PURE prompt-building and
# output-parsing pieces of that replacement; the actual model call is
# exercised via tests/test_memory.py's session-isolation tests and
# requires a live Ollama instance to test end-to-end.

def test_condense_prompt_includes_recent_history():
    history = [{"q": "how to connect hub to MyConnect app", "a": "Connect the tablet..."}]
    prompt = build_condense_prompt("give me that from step 1", history)
    assert "how to connect hub to MyConnect app" in prompt
    assert "give me that from step 1" in prompt


def test_condense_prompt_with_no_history_says_so():
    prompt = build_condense_prompt("how to connect tablet to hub", [])
    assert "first message" in prompt.lower()
    assert "how to connect tablet to hub" in prompt


def test_condense_prompt_only_uses_recent_turns():
    history = [
        {"q": "turn one", "a": "answer one"},
        {"q": "turn two", "a": "answer two"},
        {"q": "turn three", "a": "answer three"},
    ]
    prompt = build_condense_prompt("follow up", history, max_history_turns=2)
    assert "turn one" not in prompt
    assert "turn two" in prompt
    assert "turn three" in prompt


def test_parse_condense_output_strips_quotes_and_labels():
    assert parse_condense_output('"how to connect hub to MyConnect app"', "fallback") == \
        "how to connect hub to MyConnect app"
    assert parse_condense_output("Rewritten query: how to connect hub", "fallback") == \
        "how to connect hub"


def test_parse_condense_output_takes_first_line_only():
    # Reproduces the exact phi behaviour seen in production: it outputs
    # the rewritten query on line 1 then continues generating the rest
    # of the prompt template as additional lines.
    raw = 'What is the capital of France?"\n\nRules:\n1. The assistant can only respond...'
    result = parse_condense_output(raw, "fallback")
    assert "Rules" not in result
    assert "France" in result


def test_parse_condense_output_falls_back_on_empty():
    assert parse_condense_output("", "give me that from step 1") == "give me that from step 1"
    assert parse_condense_output("   ", "give me that from step 1") == "give me that from step 1"


def test_parse_condense_output_passthrough_when_clean():
    text = "how to connect hub to MyConnect app and step 1 specifically"
    assert parse_condense_output(text, "fallback") == text


# ── Bug: condense_query rewriting standalone queries ──────────────────
# Production transcript: "post installation verification installer sign
# off" was rewritten to "How to connect tablet to hub" because phi was
# called even though the query had no reference markers at all.
# has_reference_markers now guards the model call.

def test_reference_markers_fires_on_genuine_followups():
    from text_utils import has_reference_markers
    assert has_reference_markers("give me that from step 1") is True
    assert has_reference_markers("I need more context than above") is True
    assert has_reference_markers("tell me more") is True
    assert has_reference_markers("and what about the relay") is True
    assert has_reference_markers("those steps again") is True
    assert has_reference_markers("more detail on that please") is True


# ── Bug: "none" confidence band gave identical flat rejection to a ─────
# genuinely out-of-domain query AND a failed in-context follow-up.
# Production transcript: "Is there anything else I have checked the
# above and they are alright" (a real follow-up, after a MyCheckr
# registration-failure answer) got the exact same "I could not find
# that in the knowledge base." response as "what is the capital of
# france" (a fresh, standalone, genuinely out-of-domain query) — making
# the bot feel like it had no memory of the conversation at all.
# is_followup_turn() lets main.py tell these two cases apart and ask a
# clarifying question for the former while leaving the latter untouched.

def test_is_followup_turn_fires_on_actual_production_followup():
    from text_utils import is_followup_turn
    history = [{
        "q": "explain why MyCheckr registration might fail when connecting it to MyConnect",
        "a": "MyCheckr registration might fail ... due to issues with network setup or Wi-Fi connection ...",
    }]
    raw_query = "Is there anything else I have checked the above and they are alright"
    # Simulates condense_query failing to produce a useful rewrite —
    # resolved_query falls back to the raw query unchanged, and retrieval
    # on it still misses (confidence == "none" upstream in main.py).
    resolved_query = raw_query
    assert is_followup_turn(raw_query, history, resolved_query) is True


def test_is_followup_turn_false_on_standalone_query_even_with_history():
    from text_utils import is_followup_turn
    history = [{"q": "default login credentials for myconnect", "a": "user1 / password1"}]
    # "capital of France" has no reference markers and wasn't rewritten —
    # a fresh, unrelated, standalone question. Must NOT be treated as a
    # follow-up just because some history happens to exist in the session.
    assert is_followup_turn("what is the capital of france", history, "what is the capital of france") is False


def test_is_followup_turn_false_with_no_history_regardless_of_phrasing():
    from text_utils import is_followup_turn
    # Reference-marker phrasing with NO history at all (e.g. first message
    # in a fresh session) is a malformed standalone query, not a follow-up
    # — there is nothing to follow up on yet.
    assert is_followup_turn("give me that from step 1", [], "give me that from step 1") is False


def test_is_followup_turn_fires_when_condensation_rewrote_the_query():
    from text_utils import is_followup_turn
    history = [{"q": "how to connect tablet to hub", "a": "..."}]
    # Even without an obvious surface marker, if condense_query actually
    # changed the query it's a follow-up by definition.
    assert is_followup_turn("give me that", history, "how to connect tablet to MyConnect hub") is True


def test_reference_markers_does_not_fire_on_standalone_queries():
    from text_utils import has_reference_markers
    # Every one of these was INCORRECTLY rewritten by the old approach
    assert has_reference_markers("give me the checklist before leaving site after installation") is False
    assert has_reference_markers("how to connect tablet to hub") is False
    assert has_reference_markers("post installation verification installer sign off") is False
    assert has_reference_markers("what is the capital of france") is False
    assert has_reference_markers("explain why device registration might fail") is False
    assert has_reference_markers("introduction of myconnect system") is False
    assert has_reference_markers("give me steps to install and verify system is working") is False
    assert has_reference_markers("default login credentials for myconnect") is False
