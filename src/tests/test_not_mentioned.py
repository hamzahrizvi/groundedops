"""8.3 -- a "No" the product's documentation never supports becomes an honest
"the documentation doesn't mention X" (mentions.py); a "No" the manual does
document is left alone.

The corpus is faked through `lookup`, so these run without Chroma. The
answers and questions are the real ones from the 2026-09-28 labelled set
(src/eval_labels.json #30, #45) and the blind sets (MyCheckr battery, BV30
Bluetooth, SCS facial recognition), which is where the decision was taken.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mentions  # noqa: E402

# What each product's manual talks about, in the words that matter here.
MYCHECKR = ("power requirements supply voltage 12 - 24VDC current draw 570mA "
            "Ethernet connected Wi-Fi operating OSDP port USB Type-A")
MINI = ("MyCheckr Mini communicates over USB; the Ethernet and Wi-Fi "
        "configuration sections in IMS are shown as unavailable")
NV9USB = "banknote validator accepts notes; SSP ccTalk MDB via the IF5 interface"
SCS = "the SMART Hopper pays out coins; small coins below 18mm; coin routing"
BV30 = "interfaces: SSP, ccTalk, pulse; front configuration button; phone: +44"


def has(blob):
    return lambda w: mentions.word_in(blob, w)


def test_bare_no_on_a_feature_the_manual_never_mentions():
    q = ("Does the MyCheckr have a backup battery so it keeps working if the "
         "power goes out?")
    assert mentions.denied_feature("No.", q) == "backup battery"
    assert mentions.unmentioned_feature("No.", q, "mycheckr", {"mycheckr"},
                                        lookup=has(MYCHECKR)) == "backup battery"


def test_a_worded_no_names_what_it_denies():
    q = "can the NV9USB+ take coins as well as notes"
    a = "No. The NV9USB+ is a banknote validator and does not accept coins."
    assert mentions.denied_feature(a, q) == "coins"
    # "coin" is nowhere in the NV9USB+ manual: not mentioned (#30, and what
    # blind set 1 expects).
    assert mentions.unmentioned_feature(a, q, "nv9usb", {"nv9usb", "nv9", "usb"},
                                        lookup=has(NV9USB)) == "coins"


def test_a_no_that_confesses_the_absence_checks_what_was_asked():
    """S26, gpt-5-mini: the No denies a documented thing first, then admits
    the asked one is never mentioned. The asked feature is what is checked."""
    q = "Does the BV30 have a built-in touchscreen? (BV30)"
    a = ("No. The BV30 does not have a built-in cashbox and there is no "
         "mention of any built-in touchscreen; it is fitted with a 72mm bezel.")
    assert mentions.unmentioned_feature(a, q, "bv30", {"bv30"},
                                        lookup=has(BV30 + " cashbox")) == "touchscreen"


def test_a_no_the_manual_documents_stands():
    """#45: the Mini manual names Ethernet and Wi-Fi (as unavailable), so
    "No" is documented and must not become "not mentioned"."""
    q = "does mycheckr mini do ethernet or only wifi"
    a = ("No. MyCheckr Mini does not support Ethernet or Wi-Fi configuration "
         "through IMS — those network configuration sections are disabled")
    words = {"mycheckr", "mini"}
    assert mentions.unmentioned_feature(a, q, "mycheckr_mini", words,
                                        lookup=has(MINI)) is None
    assert mentions.unmentioned_feature("No.", q, "mycheckr_mini", words,
                                        lookup=has(MINI)) is None


def test_the_full_set_rule_keeps_a_no_when_one_alternative_is_documented():
    """The owner's rule: "No" stays when the manual lists the set and X is
    not in it. A denial naming a documented thing alongside an undocumented
    one is judged documented."""
    a = "No. The BV30 does not support ccTalk over Bluetooth."
    assert mentions.unmentioned_feature(a, "", "bv30", {"bv30"},
                                        lookup=has(BV30)) is None


def test_yes_and_prose_answers_are_left_alone():
    for a in ("Yes. The NV9USB+ supports MDB through the IF5 interface box.",
              "The NV9USB+ takes 12 V DC nominal.",
              "There is no separate fuse; the supply must be protected.",
              "I could not find that in the knowledge base."):
        assert mentions.denied_feature(a, "does it have a fuse") is None
        assert mentions.unmentioned_feature(a, "does it have a fuse", "nv9usb",
                                            set(), lookup=lambda w: False) is None


def test_qualifier_words_are_not_the_feature():
    a = "No, the BV30 does not support Bluetooth connectivity."
    assert mentions.feature_words("Bluetooth connectivity", {"bv30"}) == ["bluetooth"]
    assert mentions.unmentioned_feature(
        a, "Can the BV30 connect to a phone over Bluetooth for diagnostics?",
        "bv30", {"bv30"}, lookup=has(BV30)) == "Bluetooth connectivity"


def test_product_and_subject_words_are_not_the_feature():
    q = "does the SMART Coin System support facial recognition"
    words = {"smart", "coin", "system", "scs", "sku", "hopper", "twin"}
    assert mentions.denied_feature("No.", q) == "facial recognition"
    assert mentions.feature_words("SMART Coin System support facial recognition",
                                  words) == ["facial", "recognition"]
    assert mentions.unmentioned_feature("No.", q, "sku_scs", words,
                                        lookup=has(SCS)) == "facial recognition"
    # ...and a No about something the manual is full of stands.
    assert mentions.unmentioned_feature("No.", "does the SMART Coin System take coins",
                                        "sku_scs", words, lookup=has(SCS)) is None


def test_an_answer_that_denies_nothing_specific_is_left_alone():
    a = ("No. To connect an NV9USB+ to an MDB machine you must use an IF5 "
         "interface box, which regulates the power supply.")
    assert mentions.unmentioned_feature(
        a, "Can I connect the NV9USB+ directly to an MDB vending machine?",
        "nv9usb", {"nv9usb", "nv9", "usb"}, lookup=lambda w: False) is None


def test_word_forms_and_word_boundaries():
    assert mentions.word_in("accepts coins of any size", "coin")
    assert mentions.word_in("the coin path", "coins")
    assert mentions.word_in("two batteries", "battery")
    assert mentions.word_in("Wi-Fi operating", "wifi")
    assert mentions.word_in("wifi operating", "wi-fi")
    assert not mentions.word_in("telephone support", "phone")
    assert not mentions.word_in("a batter of tests", "battery")


def test_the_reply_names_the_gap_and_is_not_a_refusal_phrase():
    from text_utils import is_refusal
    r = mentions.reply("MyCheckr", "backup battery")
    assert "MyCheckr documentation doesn't mention backup battery" in r
    # The switchboard rewrites recognised refusals into the generic one;
    # this must keep its own wording, or the gap it names is lost.
    assert not is_refusal(r)


def test_kill_switch():
    # No pytest fixtures: run_tests.py calls test functions directly.
    before = os.environ.pop("NOT_MENTIONED_CHECK", None)
    try:
        assert mentions.enabled()
        os.environ["NOT_MENTIONED_CHECK"] = "off"
        assert not mentions.enabled()
    finally:
        os.environ.pop("NOT_MENTIONED_CHECK", None)
        if before is not None:
            os.environ["NOT_MENTIONED_CHECK"] = before


def test_an_auxiliary_verb_asks_about_no_feature():
    """S30, B5-19: "No, that's normal" to "have i put it in wrong?" became
    "doesn't mention i put it"."""
    q = ("fitted a lock to the front of the nv200 spectral head and the key "
         "only turns about 45 degrees. have i put it in wrong?")
    a = ("No. With the lock correctly fitted to the NV200 Spectral, the key will "
         "only turn to around 45 degrees. That is normal.")
    assert mentions.denied_feature(a, q) is None
    assert mentions.denied_feature("No.", "does it have a backup battery") == "backup battery"


def test_a_bare_pronoun_object_names_no_feature():
    """S30, N4-30: "No, the maximum is 165mm" to "will it still accept them"."""
    q = ("this takes our 168mm long notes fine. if we move to the NV4000 will it "
         "still accept them?")
    assert mentions.denied_feature("No. The NV4000 maximum note length is 165mm.", q) is None


def test_an_indefinite_pronoun_names_no_feature():
    """S30 (e), B6-45: "do i need to do ANYTHING" made "anything" the denied
    feature: "The NV4000 documentation doesn't mention anything"."""
    for a, q in (("No.", "after the update do i need to do anything?"),
                 ("No - you should not leave it.",
                  "do i need to do anything or just leave it?"),
                 ("No, it does not have anything extra.", ""),
                 ("No.", "does it have something to hold the notes?"),
                 ("No.", "can it connect to anything?")):
        assert mentions.denied_feature(a, q) is None, (a, q)
        assert mentions.unmentioned_feature(a, q, "nv4000", {"nv4000"},
                                            lookup=lambda w: False) is None
    assert mentions.denied_feature("No.", "does it have a backup battery") == "backup battery"
    # Only the pronoun names nothing: what follows it is still searched.
    for a, q in (("No.", "Can the NV4000 connect to anything over Bluetooth?"),
                 ("No.", "Does it have anything like a backup battery?")):
        phrase = mentions.denied_feature(a, q)
        assert phrase and mentions.feature_words(phrase)[-1] == ("battery" if "battery" in phrase else "bluetooth"), phrase
        assert not {"anything", "like"} & set(mentions.feature_words(phrase)), phrase
        assert mentions.unmentioned_feature(a, q, "nv4000", {"nv4000"},
                                            lookup=lambda w: False) == phrase
    assert mentions.feature_words(mentions.denied_feature(
        "No.", "do i need to do anything after loading the firmware?")) == [
        "after", "loading", "firmware"]


def test_a_connect_question_keeps_its_verb():
    """8.3 quirk: a bare "No." to "connect to a phone over Bluetooth" read
    "doesn't mention phone over Bluetooth". The searched words stay the same."""
    q = "Can the BV30 connect to a phone over Bluetooth?"
    phrase = mentions.denied_feature("No.", q)
    assert phrase == "connecting to a phone over Bluetooth"
    assert mentions.feature_words(phrase, {"bv30"}) == ["phone", "bluetooth"]
    assert ("BV30 documentation doesn't mention connecting to a phone over "
            "Bluetooth,") in mentions.reply("BV30", phrase)
    a = "No, the BV30 cannot connect to a phone over Bluetooth."
    assert mentions.denied_feature(a, "") == "connecting to a phone over Bluetooth"
    a = "No, the BV30 cannot be connected to a phone over Bluetooth."
    assert mentions.denied_feature(a, "") == "connecting to a phone over Bluetooth"
    assert mentions.denied_feature("No.", "Does the BV30 support Bluetooth?") == "Bluetooth"
