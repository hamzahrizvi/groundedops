"""D-20261030-13: the item the customer names, for the product asked, not
its look-alike.

A model code no catalogue entry has but one product's pages print names
that product ("the nv9st" -> NV9USB+); a model we do not make is said to be
unknown in a product chat too ("CSCS-9000"); and the answer prompt is told
when the named item sits only on another product's row ("bunch note
feeder" on the NV200S row) or beside a look-alike ("float by denomination"
beside "payout by denomination").
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _harness  # noqa: F401,E402 -- load main with lightweight test dependencies
import main  # noqa: E402
import clarify  # noqa: E402

NAMES = {"nv201_spectral": "NV201 Spectral", "nv200s": "NV200S",
         "compact_smart_coin_system": "Compact SMART Coin System",
         "note_val_general": "General (shared docs)"}
ALIASES = {"nv200s": ["NV200 Spectral", "NV200"], "compact_smart_coin_system": ["cSCS"]}


def _catalogue():
    return patch.object(main, "_product_names", return_value=dict(NAMES)), \
        patch.object(main, "_product_aliases", return_value=dict(ALIASES))


def _note(question, chunks, key, vocab=None):
    a, b = _catalogue()
    with a, b:
        return main._named_item_note(question, chunks, key, vocab=vocab or {})


# ── near-name model ────────────────────────────────────────────────────
def test_a_model_code_one_product_prints_names_that_product():
    known = {"cba9", "nv9usb", "nv9"}
    vocab = {"nv9st": {"nv9usb"}, "nv200": {"nv200s", "nv4000"}}
    find = lambda q: clarify.documented_model_products(q, known, vocab, {"nv", "cba"})  # noqa: E731
    assert find("cba9 or the nv9st, which is cheaper to repair") == ["nv9usb"]
    assert find("the nv200 bezel") == []           # printed by two products
    assert find("cba9 bezel") == []                # a catalogue name
    assert find("part NV02004141498000") == []     # a part number, no model


# ── phantom model ──────────────────────────────────────────────────────
def test_a_short_letter_name_is_a_model_prefix():
    pre = clarify.model_prefixes(["Compact SMART Coin System", "cSCS", "NV9 Spectral"])
    assert {"cscs", "nv"} <= pre and "compactsmartcoinsystem" not in pre
    assert clarify.unknown_model_name("wire up the CSCS-9000 Ultra", {"cscs"}, {}, pre) \
        == "CSCS-9000"
    assert clarify.unknown_model_name("part NV02004141498000", set(), {}, pre) is None
    forms = {"compact_smart_coin_system": ["Compact SMART Coin System", "cSCS"],
             "nv9_spectral": ["NV9 Spectral", "NV9S"]}
    assert clarify.close_model_names("CSCS-9000", forms) == ["Compact SMART Coin System"]


def test_an_unknown_model_is_said_in_a_product_chat():
    from test_pipeline_trace import _ask
    reply = ("I have no documentation for a product called CSCS-9000. "
             "Did you mean the Compact SMART Coin System?")

    def in_chat(req):
        req.product = "compact_smart_coin_system"
        return main.query_route(req)

    a, b = _catalogue()
    with a, b, patch.object(main, "_unknown_product_text", return_value=reply):
        out = _ask(q="how do I wire up the CSCS-9000 Ultra", entry=in_chat)
    assert out["answer"] == reply
    assert out["role"] == "clarify"


# ── near-name command ──────────────────────────────────────────────────
DEVIATIONS = {
    "id": "c1", "product": "compact_smart_coin_system", "source": "C-SCS Deviations.txt",
    "text": "Legacy commands no longer supported:\n"
            "Command 0x46 - Payout By Denomination (Coin)\n"
            "Command 0x44 - Float By Denomination is supported as before."}


def test_a_look_alike_command_beside_the_named_one_is_pointed_out():
    note = _note("does the compact still support payout by denomination",
                 [DEVIATIONS], "compact_smart_coin_system")
    assert '"payout by denomination"' in note
    assert '"float by denomination"' in note
    assert "only" in note


def test_a_named_item_the_passages_never_print_gets_the_look_alike():
    note = _note("does the compact support dispense by denomination",
                 [DEVIATIONS], "compact_smart_coin_system")
    assert 'never mention "dispense by denomination"' in note


def test_no_look_alike_no_note():
    assert _note("what is command 0x46", [DEVIATIONS], "compact_smart_coin_system") == ""
    # A different form of the same word is no look-alike.
    chunk = dict(DEVIATIONS, text="Cleaning the note path: wipe the note path lenses.")
    assert _note("how do I clean the note path", [chunk], "compact_smart_coin_system") == ""


# ── another product's row ──────────────────────────────────────────────
OWN = {"id": "o1", "product": "nv201_spectral", "source": "NV201 Manual.pdf",
       "text": "The NV201 Spectral note validator runs on 12V DC."}
FLASH = {"id": "g1", "product": "note_val_general", "source": "ITL Device Flash Codes.txt",
         "text": "4x | NV200S: Power Supply too Low (with BNF connected 24V are required) | "
                 "The Bunch Note feeder requires 24V DC.\nBunch Note Feeder"}


# What the index says each word is printed by: "connected" is the NV201's too.
VOCAB = {"bunch": {"nv200s"}, "feeder": {"nv200s"},
         "connected": {"nv200s", "nv201_spectral"}}


def test_an_item_only_on_another_products_row_is_flagged():
    note = _note("does the NV201 need 24V with the bunch note feeder connected",
                 [OWN, FLASH], "nv201_spectral", vocab=VOCAB)
    assert '"bunch note feeder"' in note
    assert "NV200S" in note and "NV201 Spectral" in note
    assert note.count("\n- ") == 1


def test_a_shared_row_that_names_the_asked_product_stays_usable():
    row = dict(FLASH, text=FLASH["text"].replace("NV200S:", "NV201:"))
    assert _note("does the NV201 need 24V with the bunch note feeder connected",
                 [OWN, row], "nv201_spectral", vocab=VOCAB) == ""


def test_an_item_the_products_own_pages_print_is_not_flagged():
    assert _note("does the NV201 need 24V with the bunch note feeder connected",
                 [OWN, FLASH], "nv201_spectral",
                 vocab={w: s | {"nv201_spectral"} for w, s in VOCAB.items()}) == ""


def test_no_note_without_one_product():
    assert _note("bunch note feeder", [FLASH], None) == ""
    assert _note("bunch note feeder", [FLASH], "note_val_general") == ""


# ── the prompt carries it ──────────────────────────────────────────────
def test_the_note_reaches_both_prompt_templates():
    for mode in ("", "short"):
        with patch.dict(os.environ, {"ANSWER_PROMPT": mode}):
            p = main.build_answer_prompt("", "ctx", "q?", note="NOTE-X")
            assert "NOTE-X" in p and p.index("NOTE-X") < p.index("Question: q?")
            assert "NOTE-X" not in main.build_answer_prompt("", "ctx", "q?")
