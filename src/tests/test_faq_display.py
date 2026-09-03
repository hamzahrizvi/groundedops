"""Which FAQ entries a visitor is allowed to SEE, as opposed to match.

Harvesting tables and checklists made every caption an FAQ "question":
"Operation: Temperature, Humidity", "Start Up Procedure: White, Green,
Purple, Blue", "Updating MyCheckr Mini checklist: 2". As retrieval keys they
work -- that is what lifted discovery answers from 3/7 to 7/9. As menu items
they are gibberish, and on guest chat the suggested-question list is the
whole interface, so 192 of 345 entries were showing up as noise.

So display and matching are separated. These tests pin both halves: the junk
stops being offered, and it does NOT stop being findable.
"""
import json
import os
import tempfile

import faq_store


REAL_QUESTIONS = [
    "What is MyCheckr?",
    "Does MyCheckr require an internet connection?",
    "How can MyCheckr be mounted?",
    "Is Ethernet required for the MyCheckr?",
    "give pinout for nv9?",
    # The regression that made me delete a rule: a "word followed by a single
    # letter" check for PDF column splits ("Informatio n") also matches every
    # ordinary use of the article "a", and hid fourteen good questions.
    "What should be sourced before installing a MyCheckr device?",
    "Is there a way to set the device to detect a person's face specifically?",
    "How do I find a device in ICU Management System?",
]

NOT_QUESTIONS = [
    "Operation: Temperature, Humidity",
    "Storage: Temperature, Humidity",
    "Start Up Procedure: White, Green, Purple, Blue",
    "Default reactions to face: Red, Green, Yellow",
    "Updating MyCheckr Mini checklist: 2",
    "Device Information, Idle Indicator, Running Mode, Device Name",
    "Upload Face, File System, Camera Capture, Process as QR Code",
    "After selecting an image, adjust the crop area before: Zoom Slider, Cancel",
    "MyConnect Quick Start Guide � Installer Edition: To be combined with",
    "short",
]


def test_real_questions_are_displayable():
    for q in REAL_QUESTIONS:
        assert faq_store.is_question_shaped(q), f"rejected a real question: {q!r}"


def test_table_captions_are_not_displayable():
    for q in NOT_QUESTIONS:
        assert not faq_store.is_question_shaped(q), f"accepted junk: {q!r}"


def test_article_a_is_not_treated_as_a_broken_word():
    """The specific false positive, kept as its own test because it cost
    fourteen good questions and would be easy to reintroduce."""
    assert faq_store.is_question_shaped(
        "What should be sourced before installing a MyCheckr device?")
    assert faq_store.is_question_shaped("Can I mount a MyCheckr on a wall?")


def test_mojibake_is_rejected():
    assert not faq_store.is_question_shaped("What is the � setting?")


def test_shape_not_origin_decides():
    """A harvested entry that reads well is still shown; a generated one that
    does not is still hidden. Provenance is not the test."""
    assert faq_store.is_displayable(
        {"origin": "harvested",
         "question": "What is the MyCheckr Mini and what does it do?"})
    assert not faq_store.is_displayable(
        {"origin": "generated",
         "question": "Date Range Selector, Time Filter, Graph View Selector"})


# ── the store-level behaviour ───────────────────────────────────────────

ENTRIES = [
    {"id": "h1", "origin": "harvested", "products": "mycheckr", "category": "",
     "question": "Operation: Temperature, Humidity", "answer": "| a | b |"},
    {"id": "g1", "origin": "generated", "products": "mycheckr", "category": "",
     "question": "Does MyCheckr need an internet connection?", "answer": "No."},
    {"id": "c1", "origin": "curated", "products": "mycheckr", "category": "",
     "question": "What is MyCheckr?", "answer": "An age estimation device."},
    # Two manuals in the corpus produce the same overview question twice.
    {"id": "h2", "origin": "harvested", "products": "mycheckr", "category": "",
     "question": "What is the MyCheckr and what does it do?", "answer": "X"},
    {"id": "h3", "origin": "harvested", "products": "mycheckr", "category": "",
     "question": "What is the MyCheckr and what does it do?", "answer": "X"},
]


def _with_store(entries):
    d = tempfile.mkdtemp(prefix="faq-disp-")
    p = os.path.join(d, "faq_store.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(entries, fh)
    faq_store._PATH = p
    faq_store._invalidate_cache()
    return p


def test_display_hides_captions_but_retrieval_keeps_them():
    old = faq_store._PATH
    try:
        _with_store(ENTRIES)
        shown = faq_store.list_for_product("mycheckr", display_only=True)
        pool = faq_store.list_for_product("mycheckr")

        qs = [e["question"] for e in shown]
        assert "Operation: Temperature, Humidity" not in qs, \
            "a table caption was offered to a visitor"
        assert len(pool) == len(ENTRIES), \
            "the retrieval pool must keep every entry -- unpresentable is " \
            "not the same as unusable"
    finally:
        faq_store._PATH = old
        faq_store._invalidate_cache()


def test_display_deduplicates_identical_questions():
    old = faq_store._PATH
    try:
        _with_store(ENTRIES)
        qs = [e["question"] for e in
              faq_store.list_for_product("mycheckr", display_only=True)]
        assert qs.count("What is the MyCheckr and what does it do?") == 1, \
            f"the same question was listed twice: {qs}"
    finally:
        faq_store._PATH = old
        faq_store._invalidate_cache()


def test_display_puts_curated_first():
    """The widget shows the first N and ranks nothing, so ordering decides
    which questions a visitor ever sees."""
    old = faq_store._PATH
    try:
        _with_store(ENTRIES)
        shown = faq_store.list_for_product("mycheckr", display_only=True)
        assert shown[0]["question"] == "What is MyCheckr?", \
            f"curated should lead, got {shown[0]['question']!r}"
        origins = [e.get("origin") for e in shown]
        assert origins.index("curated") < origins.index("harvested")
    finally:
        faq_store._PATH = old
        faq_store._invalidate_cache()
