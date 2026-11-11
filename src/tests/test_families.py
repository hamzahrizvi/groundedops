"""Product families (CEO 2026-11-05): ask which look-alike model before
answering a question that names only the family. Runs on a sample list."""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import families as F  # noqa: E402

# Not through os.environ: this file runs in pytest's own process, and every
# own-process test file after it inherited the scratch families it saved
# (test_two_products then lost its two-product turn).
F._PATH = os.path.join(tempfile.mkdtemp(), "families.json")
# A new install starts with no families; these tests run on a sample list
# (made-up data for the logic, not a corpus).
shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                         "families_fixture.json"), F._PATH)


def _asked(r):
    return r["ask"] and r["ask"]["family"]["name"]


def test_a_family_name_alone_is_asked_about():
    r = F.resolve("how tall is the smart hopper?")
    assert _asked(r) == "SMART Hopper" and r["products"] == []
    assert len(F.options(r["ask"]["family"], r["ask"]["offer"], "q")) == 4  # 2 + general + not sure
    assert _asked(F.resolve("what is the ICU's screen size")) == "ICU"
    assert _asked(F.resolve("nv200 power supply")) == "NV200"


def test_a_model_named_is_answered_for_its_product():
    for q, want in (("smart hopper 3 dimensions", ["smart_hopper_3"]),
                    ("does the SH4 take 24V", ["sku_scs"]),
                    ("nv11 spectral bezel", ["nv9_spectral"]),
                    ("NV22 note capacity", ["nv9_spectral"]),
                    ("ICU Pro sizing", ["icu"]),
                    ("smart payout cable", ["nv200s"])):
        r = F.resolve(q)
        assert not r["ask"] and r["products"] == want, (q, r)


def test_the_chats_own_product_settles_it_unless_it_is_shared():
    # The bug: "smart hopper" in a Smart Hopper 3 chat answered for the SH4.
    assert not F.resolve("how tall is the smart hopper", product="smart_hopper_3")["ask"]
    assert "smart hopper" not in F.without_generic("how tall is the smart hopper")
    assert "smart hopper 3" in F.without_generic("how tall is the smart hopper 3")
    # One product behind every model: its name still names it.
    assert "smart coin system" in F.without_generic("compare the sh3 and the smart coin system")
    # ICU Lite and Pro share "icu": still asked, and only those offered.
    r = F.resolve("icu sizing", product="icu")
    assert _asked(r) == "ICU"
    r = F.resolve("nv9 power", product="nv9usb")
    names = [r["ask"]["family"]["members"][i]["name"] for i in r["ask"]["offer"]]
    assert names == ["NV9USB+", "NV11"], names


def test_a_chat_on_another_product_is_not_asked_about():
    # D-20261107-02: the chat's product is no member of the family it names:
    # named in passing, or the chat product's own range the list lacks.
    for q, p in (("is a g-scs ok in a kiosk at 1 degree?", "global_smart_coin_system"),
                 ("how do i split the compact coin system?", "compact_smart_coin_system"),
                 ("da3 went red halfway through updating an NV9", "da3"),
                 ("mine flashes 1 long 1 short, on his nv9 that means the path is open", "nv10usb"),
                 ("red 5 flashes on the plain hopper, not the coin system", "smart_hopper_3"),
                 ("which calibration paper for an NV200 Spectral?", "da3"),
                 ("does the icu fit beside the mycheckr", "mycheckr")):
        assert not F.resolve(q, product=p)["ask"], (q, p)
    # A product the message names that is no member is what it asks about.
    assert not F.resolve("da3 went red updating an NV9", named=["da3"])["ask"]
    # Still asked: nothing picked or named, or a chat product several share.
    assert _asked(F.resolve("nv9 calibration paper")) == "NV9"
    assert _asked(F.resolve("nv200 power supply", product="nv200s")) == "NV200"
    assert _asked(F.resolve("nv200 power supply", named=["nv200s"])) == "NV200"


def test_a_pick_and_a_general_question_are_answered():
    asked = [{"q": "nv11 weight", "a": "Which NV9 do you have? ... " + F.MARKER}]
    # Straight after we asked, a generic member name ("NV11" is inside
    # "NV11 Spectral") is the pick.
    r = F.resolve("nv11 weight (NV11)", asked)
    assert not r["ask"] and r["products"] == ["nv9usb"], r
    assert _asked(F.resolve("nv11 weight")) == "NV9"
    fam = next(f for f in F.families() if f["key"] == "icu")
    general = F.options(fam, [0, 1], "icu sizing")[-2]["send"]
    r = F.resolve(general)
    assert not r["ask"] and r["products"] == ["icu"], (general, r)
    # A later question naming only the family keeps the choice.
    history = asked + [{"q": "nv11 weight (NV11)", "a": "It weighs 1 kg."}]
    r = F.resolve("how do I clean the nv11", history)
    assert not r["ask"] and r["products"] == ["nv9usb"], r
    # Never asked twice in a row.
    assert not F.resolve("the nv9", asked)["ask"]


def test_a_family_named_like_its_member_keeps_that_name():
    """The auto family "MyCheckr" is also the MyCheckr's own name: "the
    MyCheckr and the ICU Pro" still names the MyCheckr."""
    fam = {"key": "mycheckr", "name": "MyCheckr", "auto": True,
           "members": [{"name": "MyCheckr", "product": "mycheckr"},
                       {"name": "MyCheckr Mini", "product": "mycheckr_mini"}]}
    real = F.families
    F.families = lambda: [fam]
    try:
        assert "mycheckr" in F.without_generic("the MyCheckr and the ICU Pro")
    finally:
        F.families = real


def test_a_comparison_is_not_asked_about():
    assert not F.resolve("nv9 usb vs the nv9s", named=["nv9usb", "nv9_spectral"])["ask"]
    assert not F.resolve("nv9 or the spectral one?", named=["nv9usb", "nv9_spectral"])["ask"]
    assert not F.resolve("compare the SH3 and the SMART Coin System",
                         named=["smart_hopper_3", "sku_scs"])["ask"]


def test_not_sure_gets_the_differences():
    for q in ("I'm not sure which SMART Hopper I have", "how do I tell which hopper this is",
              "idk which one", "which model do I have?"):
        assert F.confused(q), q
    assert not F.confused("how do I clean the hopper")
    fam = F.family_for("not sure which smart hopper I have", None, None)
    text = F.guide_reply(fam, [0, 1])
    assert "SD card slot" in text and F.MARKER[1:] in text
    assert F.picked_reply("I have the SMART Hopper 3", [{"q": "x", "a": text}]).startswith(
        "Thanks, that's the SMART Hopper 3.")
    assert F.picked_reply("I have the SMART Hopper 3", None) is None


def test_products_named_alike_are_grouped():
    groups = dict((n, sorted(k)) for n, k in F._auto_groups({
        "nv9usb": "NV9USB+", "nv9s": "NV9 Spectral", "bv30": "BV30",
        "m": "MyCheckr", "mm": "MyCheckr mini", "scs": "SMART Coin System",
        "g": "Global SMART Coin System"}))
    assert groups == {"NV9": ["nv9s", "nv9usb"], "MyCheckr": ["m", "mm"],
                      "SMART Coin System": ["g", "scs"]}, groups


def test_the_console_edit_is_checked():
    out = F.validate([{"name": "ICU", "members": [{"name": "ICU Lite", "product": "icu",
                                                   "aliases": [" ", "lite"]}]}])
    assert out[0]["key"] == "icu" and out[0]["members"][0]["aliases"] == ["lite"]
    for bad in ([{"name": ""}], [{"name": "A"}, {"name": "a"}],
                [{"name": "A", "members": [{"name": ""}]}]):
        try:
            F.validate(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_not_sure_asks_one_question_at_a_time():
    fam = next(f for f in F.families() if f["key"] == "nv9")
    step = F.guide_start("g1", fam, list(range(5)), "nv9 power?")
    # The question that best halves five, the first of equals: note float (2 of 5).
    assert step["answer"].startswith("Is a note float"), step
    assert [o["send"] for o in step["options"]] == ["Yes", "No", "Not sure"]
    step = F.guide_step("g1", "No")
    assert step["answer"] == "Is a multi-note float fitted on top?", step
    step = F.guide_step("g1", "no")
    assert step["answer"] == "Does its label say Spectral?", step
    step = F.guide_step("g1", "yes")
    assert step["answer"].startswith("That sounds like the NV9 Spectral.") and F.MARKER in step["answer"]
    assert step["options"][0]["send"] == "nv9 power? (NV9 Spectral)", step["options"]
    assert F.guide_step("g1", "yes") is None              # finished
    # "Not sure" skips a question; anything else ends the questions.
    F.guide_start("g2", fam, list(range(5)), None)
    assert F.guide_step("g2", "not sure")["answer"] == "Does its label say Spectral?"
    assert F.guide_step("g2", "how do I clean it") is None and "g2" not in F._guides
    # No question tells the rest apart: every difference is listed.
    icu = next(f for f in F.families() if f["key"] == "icu")
    assert F.guide_start("g3", icu, [0, 1], None)["answer"].startswith("Here is how to tell")
    assert F.show_all("Show me every NV9")["key"] == "nv9"

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
