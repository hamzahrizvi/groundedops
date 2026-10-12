"""D-20261009-10: a vague fault report gets the manual's checklist.

"nv4000 is showing an error" in a product chat was answered from whatever
the top passages held (a misread flash-code table). clarify.vague_shape
bounds which first messages are shown to the model; checklist_for lets it
pick among the manual's own fault headings; checklist_reply composes the
reply from those headings and pages only. A model name none of the
products has ("nv300") is said to be unknown.
"""
import inspect
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _harness  # noqa: F401,E402 -- load main with lightweight test dependencies
import clarify  # noqa: E402
import llm  # noqa: E402
import main  # noqa: E402

PRODUCT_WORDS = {"nv4000", "nv9", "spectral", "mycheckr", "mini", "myconnect"}
SECTIONS = [
    {"heading": "Bezel/Status LED Flash Codes", "page": 51, "source": "NV4000.pdf",
     "chunks": [{"id": "a", "text": "Red flashing...", "source": "NV4000.pdf", "page": 51}]},
    {"heading": "NV4000 Module Flash Codes", "page": 52, "source": "NV4000.pdf",
     "chunks": [{"id": "b", "text": "Module LED...", "source": "NV4000.pdf", "page": 52}]},
    {"heading": "Testing after an error has been cleared", "page": 65,
     "source": "NV4000.pdf", "chunks": []},
]


def _pick(reply, sections=SECTIONS, provider="deepseek"):
    seen = []

    def fake(role, prompt, **k):
        seen.append(prompt)
        return {"text": reply, "provider": provider, "model": "deepseek-chat"}
    with mock.patch.object(sys.modules.get("llm", llm), "generate_with_fallback", fake):
        out = clarify.checklist_for("nv4000 is showing an error", "NV4000", sections)
    return out, seen


def test_vague_reports_pass_the_shape():
    for q in ("mycheckr isnt working properly, can you help",
              "its showing the wrong thing on the screen, can you fix it",
              "spectral isnt working right after the update, help",
              "nv4000 is showing an error, what does it mean?",
              "my mycheckr isnt working right, what should i do?"):
        assert clarify.vague_shape(q, PRODUCT_WORDS), q


def test_codes_and_long_questions_do_not():
    for q in ("bezel 2 long 3 short", "red led flashing 7 times",
              "what does error code E12 mean",
              "how do I configure the escrow timeout for rejected notes on startup"):
        assert not clarify.vague_shape(q, PRODUCT_WORDS), q


def test_a_pick_returns_the_chosen_headings_in_order():
    out, seen = _pick("CHECK 2,1")
    assert out == ([SECTIONS[1], SECTIONS[0]], "deepseek-chat")
    assert "1. Bezel/Status LED Flash Codes" in seen[0] and "NV4000" in seen[0]
    assert _pick("**CHECK: 1**")[0][0] == [SECTIONS[0]]
    assert _pick("CHECK 3, 3, 1")[0][0] == [SECTIONS[2], SECTIONS[0]]


def test_anything_else_answers_as_before():
    for reply in ("ANSWER", "", "CHECK 9", "CHECK", "I think 1 and 2"):
        assert _pick(reply)[0] is None, reply
    assert _pick("CHECK 1", provider="none")[0] is None
    assert _pick("CHECK 1", sections=[])[0] is None


def test_the_reply_is_only_the_headings_and_pages():
    text = clarify.checklist_reply("NV4000", SECTIONS[:2])
    assert "- Bezel/Status LED Flash Codes (page 51)" in text
    assert "- NV4000 Module Flash Codes (page 52)" in text
    assert "Red flashing" not in text and "Testing after" not in text
    assert text.rstrip().endswith("contact support if none does.")
    # The next turn must not ask back again.
    assert main._asked_to_clarify_last_turn([{"q": "x", "a": text}])


def test_fault_sections_dedupes_and_skips_command_headings():
    chunks = [
        {"product": "nv4000", "section": "Fault Finding › Bezel/Status LED Flash Codes", "page": 51},
        {"product": "nv4000", "section": "Fault Finding › Bezel/Status LED Flash Codes", "page": 53},
        {"product": "nv4000", "section": "Poll 0x07 › Error response", "page": 90},
        {"product": "nv4000", "section": "Cleaning the Product", "page": 40},
        {"product": "nv200s", "section": "Main Error Code List", "page": 67},
        {"product": "nv4000,biometrics_general", "section": "📩 Module Flash Codes", "page": 52},
        {"product": "nv4000", "section": "Default Configuration", "page": 10},
    ]
    retrieved = [{"product": "nv4000", "section": "Mounting", "page": 5}]
    out = clarify.fault_sections("nv4000", retrieved, chunks=chunks)
    assert [(s["heading"], s["page"]) for s in out] == [
        ("Mounting", 5),
        ("Fault Finding: Bezel/Status LED Flash Codes", 51),
        ("Module Flash Codes", 52)]
    assert len(out[1]["chunks"]) == 2


def test_an_unknown_model_name_is_found():
    vocab = {"nv200": {"nv200s"}, "nv11": {"nv9usb"}}
    known = {"nv4000", "nv9", "nv9s", "bv30"}
    pre = clarify.model_prefixes(["NV9 Spectral", "NV4000", "BV30", "MyCheckr"])
    assert pre == {"nv", "bv"}
    find = lambda q: clarify.unknown_model_name(q, known, vocab, pre)  # noqa: E731
    assert find("whats the cashbox capacity on the nv300 quantum recycler?") == "NV300"
    for q in ("nv9s weight", "nv200 bezel", "the nv11 status led", "works on win10",
              "nv4000 cashbox", "bv30 pinout"):
        assert find(q) is None, q
    text = clarify.unknown_product_reply("NV300", ["NV4000", "BV30", "MyCheckr"])
    assert text.startswith("I don't have a product called NV300.")
    assert "NV4000, BV30 and MyCheckr" in text


def test_the_gate_is_scoped_first_turn_only():
    code = inspect.getsource(main.query)
    assert "_symptom_open = (not history and not _comparing" in code
    assert code.count("_vague_fault_checklist(") == 2
    assert code.index("_vague_fault_checklist(") < code.index('if confidence == "none":')


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
