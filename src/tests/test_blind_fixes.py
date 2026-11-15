"""S30: fixes found by blind sets 4, 5 and NV4000 (2026-10-07).

Imports main, so the suite runs it in its own process; no pytest fixtures.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def test_a_yes_that_explains_a_no_becomes_no():
    """N4-15: 3.3V against a +3.7V logic-high minimum."""
    a = ("Yes. The NV4000 defines Logic High on its inputs as +3.7V to +12V, "
         "so a 3.3V drive does not meet the documented Logic High minimum.")
    assert main._agree_verdict(a).startswith("No. The NV4000")
    assert main._strip_preamble(a).startswith("No.")
    assert main._agree_verdict(
        "Yes. Logic High is +3.7V to +12V, so 3.3V falls below the documented "
        "logic-high range and would not be read as high.").startswith("No.")
    # A conditional limit, or a real Yes, is left alone.
    for keep in ("Yes, as long as the supply does not exceed the maximum of 26.4V.",
                 "Yes. It accepts 12V and 24V supplies.",
                 "Yes, if the input is not below the minimum of 3.7V.",
                 "No. 3.3V does not meet the minimum."):
        assert main._agree_verdict(keep) == keep, keep


def test_a_passage_is_labelled_with_its_documents_product():
    """N4-29/N4-30: the NV4000 manual calls its head "NV200 Spectral"."""
    names = {"nv4000": "NV4000", "nv200s": "NV200 Spectral"}
    assert main._passage_product({"product": "nv4000"}, names) == \
        ", from the NV4000 documentation"
    # Shared or multi-product documents, and unknown keys, carry no label.
    for chunk in ({"product": "nv4000,nv200s"}, {"product": "note_val_general"},
                  {"product": "gone"}, {}):
        assert main._passage_product(chunk, names) == "", chunk


def test_the_quantity_arm_matches_the_unit_asked():
    """N4-29/N4-30: "12v rail" finds "+24VDC", "168mm" finds "165mm"."""
    import retrieval_db as R
    assert R._units("can the nv4000 go on the same 12v rail?") == {"v"}
    assert R._units("Supply Voltage (VDC) | +21.6VDC | +24VDC") == {"v"}
    assert R._units("our 168mm long notes") == {"mm"}
    assert R._units("error 5 after 3 flashes") == set()


def test_a_passage_is_labelled_with_its_heading():
    """D-20261016-15: look-alike tables differ only by their heading."""
    assert main._passage_heading({"section": "Power Requirements › Supply Currents › NV11+"}) == \
        ', under "Power Requirements › Supply Currents › NV11+"'
    assert main._passage_heading({"section": 'a "b" [c]\n d'}) == ", under \"a 'b' (c) d\""
    for chunk in ({"section": ""}, {"section": None}, {}):
        assert main._passage_heading(chunk) == "", chunk
    names = {"nv9usb": "NV9USB+"}
    lab = main._verify_label({"source": "M.pdf", "page": 14, "product": "nv9usb",
                              "section": "Supply Currents › NV11+"}, names)
    assert lab == '[M.pdf, page 14, from the NV9USB+ documentation, under "Supply Currents › NV11+"]\n', lab
    p = main.build_answer_prompt("", "[Passage 1 of 1]\nx", "q")
    assert "Take a figure only from a passage whose heading matches" in p


def test_the_answer_shows_the_picture_the_model_read_it_from():
    """CEO 2026-11-04: a picture description is labelled, the model names the
    picture it answered from, and that line is never shown to the customer."""
    fig = {"id": "M.pdf:abc:figtext:3", "source": "M.pdf", "figures": "p18_2.png"}
    page = {"id": "M.pdf:abc:7", "source": "M.pdf", "figures": "p18_1.png,p18_2.png"}
    assert main._passage_picture(fig) == ", picture 'p18_2'"
    assert main._passage_picture(page) == ""
    p = main.build_answer_prompt("", "[Passage 1 of 1, picture 'p18_2']\nx", "q")
    assert "PICTURE: NAME" in p
    assert "PICTURE: NAME" not in main.build_answer_prompt("", "[Passage 1 of 1]\nx", "q")
    raw = "It is 285 mm tall.\n\nPICTURE: p18_2"
    assert main._strip_preamble(raw) == "It is 285 mm tall."
    assert main._answer_picture(raw, [page, fig]) == ("M.pdf", "p18_2.png")
    # A made-up name, or a picture only on a page passage, shows nothing.
    assert main._answer_picture("x\nPICTURE: p99_1", [page, fig]) is None
    assert main._answer_picture("x\nPICTURE: p18_1", [page, fig]) is None
    assert main._strip_preamble("see the PICTURE: label") == "see the PICTURE: label"
    # Lab 2026-11-05: written after the sentence, naming no picture it saw.
    assert main._strip_preamble("It is the bezel release arm. PICTURE: NV9USB+") == \
        "It is the bezel release arm."
    assert main._answer_picture("x. PICTURE: NV9USB+", [page, fig]) is None
    assert main._answer_picture("285 mm. PICTURE: p18_2", [page, fig]) == ("M.pdf", "p18_2.png")


def test_a_figure_comes_from_the_passage_naming_the_set_up():
    """D-20261112-04: two figures for one thing; the set-up's passage wins."""
    p = main.build_answer_prompt("", "[Passage 1 of 1]\nx", "q")
    assert "take the figure from the passage that names the customer's set-up" in p
    assert "give both and say they differ" not in p


def test_a_document_titled_by_the_questions_code_gets_a_slot():
    """D-20261112-04: the IF17 sheet ranked 13th of 19 and was cut."""
    from unittest.mock import patch
    ranked = [{"id": "m1", "source": "SMART Hopper 3 User Manual-v1.pdf"},
              {"id": "m2", "source": "NV200 Spectral Manual.pdf"},
              {"id": "i1", "source": "IF17 - Improved USB Stability.txt"},
              {"id": "i2", "source": "IF17 - Improved USB Stability.txt"},
              {"id": "i3", "source": "IF17 - Improved USB Stability.txt"}]
    q = "smart hopper through an IF17 usb link and an nv200, how long can the usb cable be?"
    with patch.object(main, "_catalogue_terms", lambda: ["smart_hopper_3", "NV200 Spectral", "nv200"]):
        got = main._titled_for(q, ranked, {"m1"})
        assert [r["id"] for r in got] == ["i1", "i2"], got          # code match, capped at 2
        assert main._titled_for(q, ranked, {"i1", "i2", "i3"}) == []  # already in the context
        assert main._titled_for("how long can the usb cable be?", ranked, set()) == []
        # a catalogue product's code is never a title match
        assert main._titled_for("nv200 usb cable length", ranked, set()) == []
    assert main._codes("IF17 - Improved USB Stability") == {"if17"}


def test_a_named_quantity_finds_its_limit_rows():
    """D-20261114-06: a question that names a quantity but gives no value."""
    import retrieval_db as R
    u, w = R._named_quantity("what current does it pull when idle vs when its actually running")
    assert u == {"a", "ma"} and w == {"current"}, (u, w)
    bv30 = ("[BV30 — Power Requirements — Supply Currents]\nSupply Current: Maximum\n"
            "Standby: 0.15A\nRunning: 0.54A\nPeak: 1.50A")
    assert R._limit_row(bv30, u, w)
    # a measured test table has the unit but no limit: not a spec row
    assert not R._limit_row("Table 1: Average Current Comparison\n545187 | 0.19A | 0.18A", u, w)
    u, w = R._named_quantity("will the device take username 'admin' or is there a minimum length?")
    assert R._limit_row("Username | String | Must be a string of length <= 50 and >= 7", u, w)
    assert not R._limit_row("Username | String | The new API username.", u, w)
    for q in ("what's the current firmware version?", "need to size a psu", "how do I clear a jam?"):
        assert R._named_quantity(q) == (set(), set()), q
    # a value still works as before
    assert R._limit_row("Supply Voltage (VDC) | Minimum | +21.6VDC", R._units("our 12v rail"), set())


def test_a_comparison_searches_when_one_manual_matches_the_ask_better():
    """D-20261114-06: the shared 50 mA sink row answered a running-current question."""
    import sales
    idx = [
        {"product": "a", "source": "A.pdf", "page": 27, "table": "Interface logic levels",
         "attribute": "Maximum Current Sink", "values": ["50mA per Output"]},
        {"product": "b", "source": "B.pdf", "page": 15, "table": "Interface Logic Levels",
         "attribute": "Maximum Current Sink", "values": ["50mA per Output"]},
        {"product": "a", "source": "A.pdf", "page": 26, "table": "Supply currents",
         "attribute": "Running", "values": ["12V", "2 A", "Running"]},
        {"product": "b", "source": "B.pdf", "page": 14, "table": "B", "attribute": "Running", "values": ["1.0A"]},
    ]
    names = {"a": "Alpha", "b": "Beta"}
    assert sales.compare("psu sizing, whats the running current on the Alpha at 12V vs the Beta?",
                         idx, ["a", "b"], names) is None
    out = sales.compare("whats the max current sink on the Alpha vs the Beta?", idx, ["a", "b"], names)
    assert out and "50mA per Output" in out["answer"], out


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
