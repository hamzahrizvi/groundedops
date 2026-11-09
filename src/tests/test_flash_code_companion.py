"""D-20261030-14: a flash-code question follows its table through every
continuation and gets the section the asked row points at ("...in Power
Requirements"). Shapes copied from the live BV30 and NV4000 manuals.

unittest.mock.patch, not pytest fixtures: this file imports retrieval_db,
which run_tests runs in its own process with no fixtures injected.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import retrieval_db as rdb

BV = "BV30.pdf:aaaa:"
NV = "NV4000.pdf:bbbb:"
PR = "Power Requirements"


def _c(src, n, page, section, body):
    head = f"[{src} — {section}]" if section else f"[{src}]"
    return {"id": f"{src}:x:{n}", "text": f"{head}\n{body}", "source": src,
            "page": page, "section": section}


BV30 = [
    _c("BV30.pdf", 12, 8, f"{PR} › Supply Voltages",
       "Supply Voltage | Minimum | Nominal | Maximum\n"
       "Supply Voltage (V DC) | +10.8V DC | +12V DC | +13.2V DC"),
    _c("BV30.pdf", 13, 8, f"{PR} › Supply Currents", "Standby: 0.15A\nPeak: 1.50A"),
    _c("BV30.pdf", 87, 31, "Clearing a Jam", "Pull the banknote out."),
    _c("BV30.pdf", 88, 31, "Bezel Flash Codes",
       "Refer to the table below:\n"
       "Red Flashes | Blue Flashes | Error | Recommend Action\n"
       "1 | 2 | Note Path Jam | Clear any jammed notes as per Clearing a Jam\n"
       "3 | 1 | Firmware Checksum | Reprogram the firmware"),
    _c("BV30.pdf", 89, 32, "Bezel Flash Codes",
       "Red Flashes | Blue Flashes | Error | Recommend Action\n"
       "3 | 2 | Interface Checksum | Reprogram the firmware\n"
       "4 | 1 | PSU Voltage too Low | Check the power supply meets the "
       "specification in Power Requirements\n"
       "4 | 2 | PSU Voltage too High | Check the power supply meets the "
       "specification in Power Requirements"),
]

NV4000 = [
    _c("NV4000.pdf", 30, 20, f"{PR} › Supply Voltages",
       "Supply Voltage | Minimum | Nominal | Maximum\n"
       "Supply Voltage (VDC) | +21.6VDC | +24VDC | +26.4VDC"),
    _c("NV4000.pdf", 31, 20, f"{PR} › Supply Currents", "The maximum current is 7A."),
    _c("NV4000.pdf", 98, 51, "Bezel/Status LED Flash Codes",
       "Flashes |  | Indicated Status / Error | Recommended Action\n"
       "Red | Blue |  |\n"
       "1 | 1 | Note Path Open | Close the lid.\n"
       "2 | 1 | Cashbox Removed | Insert the cashbox, on the NV4000 the"),
    _c("NV4000.pdf", 99, 51, "Bezel/Status LED Flash Codes",
       "Removed | Insert the cashbox, on the NV4000 the interface checks the sensor.\n"
       "| 2 | Cashbox Jam | Remove the jam from inside the cashbox.\n"
       "3 | 1 | Firmware Checksum Error | Retry the download."),
    _c("NV4000.pdf", 100, 52, "",
       "| 2 | Interface Checksum Error | Download with the IF file.\n"
       "4 | 1 | Power Supply too Low | Check the voltage is within the range "
       "as outlined in Power Requirements in NV4000 Range Technical Data\n"
       "| 2 | Power Supply too High | Check the voltage is within the range "
       "as outlined in Power Requirements in NV4000 Range Technical Data"),
]

NV9 = [
    _c("NV9.pdf", 5, 8, f"{PR} › Supply Voltages", "Supply Voltage | 12V"),
    _c("NV9.pdf", 40, 14, "Bezel Flash Codes",
       "Red | Blue | Error\n4 | 1 | PSU too Low | Check the supply voltage"),
]


def _run(all_chunks, top_ids, query, **kw):
    by = {c["id"]: c for c in all_chunks}
    with patch.object(rdb, "get_collection", lambda: object()), \
         patch.object(rdb, "_get_bm25_index", lambda col: (None, all_chunks)):
        return rdb.complete_tables([dict(by[i]) for i in top_ids], query=query, **kw)


def _ids(out, by=None):
    return [c["id"].rsplit(":", 1)[1] for c in out
            if by is None or c.get("fetched_by") == by]


def test_bv30_asked_row_brings_power_requirements():
    out = _run(BV30, ["BV30.pdf:x:88"],
               "bv30 bezel keeps flashing red 4 times then blue 1")
    assert _ids(out) == ["88", "89", "12"]
    assert _ids(out, "section_reference") == ["12"]
    assert out[2]["retrieval_score"] == 0.0


def test_nv4000_chain_reaches_page_52_and_its_section():
    out = _run(NV4000, ["NV4000.pdf:x:98"],
               "our nv4000 bezel keeps flashing 4 red then 1 blue, PSU?")
    assert _ids(out) == ["98", "99", "100", "30"]
    assert all(c["with_previous"] for c in out[1:3])
    assert _ids(out, "section_reference") == ["30"]


def test_merged_first_cell_row_is_matched():
    out = _run(NV4000, ["NV4000.pdf:x:98"], "nv4000 blinking red four blue two")
    assert _ids(out, "section_reference") == ["30"]


def test_without_flash_words_behaviour_is_unchanged():
    q = "our nv4000 bezel shows 4 red then 1 blue"
    assert _ids(_run(NV4000, ["NV4000.pdf:x:98"], q)) == ["98", "99"]
    assert _ids(_run(NV4000, ["NV4000.pdf:x:98"], None)) == ["98", "99"]


def test_row_not_asked_fetches_no_power_section():
    out = _run(BV30, ["BV30.pdf:x:88"], "bv30 flashes 1 red 2 blue")
    assert "12" not in _ids(out) and "13" not in _ids(out)
    assert _ids(out, "section_reference") == ["87"]   # its own row's pointer


def test_row_without_pointer_fetches_nothing():
    out = _run(NV9, ["NV9.pdf:x:40"], "nv9 flashing 4 red 1 blue")
    assert _ids(out) == ["40"]


def test_opening_chunk_only_and_within_budget():
    by = {c["id"]: c for c in BV30}
    t = len(by["BV30.pdf:x:12"]["text"])
    for budget, want in ((t + 5, ["12"]), (t - 1, [])):
        out = rdb._referenced_sections(
            [dict(by["BV30.pdf:x:89"])], "flashing 4 red 1 blue", BV30,
            budget=budget)
        assert _ids(out, "section_reference") == want


# D-20261105-08: a flash question that names a product and a count reads the
# flash-code document's table for that product first, and the product's own
# flash tables with that count last. Shapes from the live "ITL Device Flash
# Codes" document, the BV50 Dipswitch guide (PDF text) and the BV50 manual.
FD = "ITL Device Flash Codes.txt"


def _p(src, n, body, product):
    return dict(_c(src, n, 1, "", body), product=product)


FLASH_DOC = [
    _p(FD, 0, "Smart Hopper Range\n"
              "LED Colour | Status (Flashes) | Description\n"
              "RED | 3x | No Encryption Key set\nRED | 4x | Coin Jam\n"
              "SMART Coin System Range\n"
              "LED Colour | Status (Flashes) | Description | Recommended Action\n"
              "RED | 4x | Feeder Calibration Error | Check the feeder", "coin_hoppers_general"),
    _p(FD, 1, "BV Range (BV20, BV30, BV50 and BV100)\n"
              "LED Colour Status (Flashes) | Description | Recommended Action\n"
              "Long | Short |\n"
              "1x | 3x | Unit not Initialised | Contact ITL\n"
              "4x | BV50, BV100: Cashbox Jam | Check the cashbox\n"
              "4x | 1x | Power Supply to Low | Check power supply", "note_val_general"),
]
BV50 = [
    _p("BV50 User Manual.txt", 44, "Count the bezel flash sequence.\n"
       "Flashes | Interface\n1 | SSP\n2 | Pulse\n3 | MDB\n4 | IF3", "bv50"),
    _p("BV50 User Manual.txt", 60, "Cleaning the note path.\nUse a dry cloth.", "bv50"),
    _p("BV50 Guide.pdf", 8, "Flash Error\n2 Invalid card read.\n"
       "3 No interface selection was detected on card.\n"
       "4 Multiple interface selection detected.\n"
       "5 Invalid interface selected.", "bv50"),
    _p("BV50 Guide.pdf", 7, "4 - Click Function button, the bezel LEDs flash.\n"
       "5 - Enter card.\n6 - Card will be rejected.", "bv50"),
]
HOPPER = [_p("SMART Hopper 3 Manual.pdf", 30, "Clearing a jam: turn the disk.", "smart_hopper_3")]
NAMES = {"bv50": ["BV50"], "smart_hopper_3": ["SMART Hopper 3"], "nv9usb": ["NV9USB+"]}
ALL = FLASH_DOC + BV50 + HOPPER


def _flash_run(top_ids, query, chunks=ALL, excluded=(), **kw):
    rdb._flash_docs_cache.update(key=None, docs={})
    with patch.object(rdb, "_catalog_names", lambda: NAMES), \
         patch.object(rdb, "is_excluded", lambda s: s in excluded):
        return _run(chunks, top_ids, query, **kw)


def _src(out, by):
    return [(c["source"], c["id"].rsplit(":", 1)[1]) for c in out if c.get("fetched_by") == by]


def test_the_products_flash_code_table_comes_first():
    out = _flash_run(["BV50 User Manual.txt:x:44"],
                     "config card spat out of the bv50 and the bezel flashed 4 times")
    assert out[0]["id"] == f"{FD}:x:1" and out[0]["fetched_by"] == "flash_code_document"
    assert out[0]["retrieval_score"] == 0.0
    assert out[1]["id"] == "BV50 User Manual.txt:x:44"
    # the Smart Hopper / SCS chunk has a "4x" row too, for another product
    assert f"{FD}:x:0" not in [c["id"] for c in out]
    # the product's own second flash table, appended; not a numbered step list
    assert _src(out, "flash_table") == [("BV50 Guide.pdf", "8")]
    assert out[-1]["id"] == "BV50 Guide.pdf:x:8"


def test_a_title_without_a_code_names_its_product():
    q = "our smart hopper is flashing red 4 times, coins stuck (SMART Hopper 3)"
    out = _flash_run(["SMART Hopper 3 Manual.pdf:x:30"], q)
    assert _src(out, "flash_code_document") == [(FD, "0")]
    # "(SMART Hopper 3)" names the product; it is not a count of 3
    with patch.object(rdb, "_catalog_names", lambda: NAMES):
        assert rdb._flash_tables([], "smart hopper (SMART Hopper 3) flashing",
                                 ALL, {c["id"]: c for c in ALL}) == []


def test_no_product_or_no_count_adds_nothing():
    for ids, q in ((["BV50 User Manual.txt:x:44"], "the bezel flashed 4 times, what is it"),
                   (["BV50 User Manual.txt:x:44"], "my bv50 bezel keeps flashing, why")):
        with patch.object(rdb, "_flash_tables", lambda out, *a, **k: out):
            want = _flash_run(ids, q)
        assert _flash_run(ids, q) == want


def test_excluded_sources_are_never_added():
    q = "bv50 flashed 4 times"
    out = _flash_run(["BV50 User Manual.txt:x:44"], q, excluded={FD, "BV50 Guide.pdf"})
    assert [c["id"] for c in out] == ["BV50 User Manual.txt:x:44"]


def test_the_budget_is_shared_document_first():
    by = {c["id"]: c for c in ALL}
    doc = len(by[f"{FD}:x:1"]["text"])
    with patch.object(rdb, "_catalog_names", lambda: NAMES), \
         patch.object(rdb, "is_excluded", lambda s: False):
        rdb._flash_docs_cache.update(key=None, docs={})
        out = rdb._flash_tables([dict(by["BV50 User Manual.txt:x:44"])],
                                "bv50 flashed 4 times", ALL, by, budget=doc + 5)
        assert [c.get("fetched_by") for c in out] == ["flash_code_document", None]
        out = rdb._flash_tables([dict(by["BV50 User Manual.txt:x:44"])],
                                "bv50 flashed 4 times", ALL, by, budget=doc - 1)
        assert _src(out, "flash_table") == [("BV50 Guide.pdf", "8")]
        assert not _src(out, "flash_code_document")


def test_a_document_not_titled_for_flash_codes_is_not_the_flash_document():
    renamed = [dict(c, source="Device Status Tables.txt",
                    id=c["id"].replace(FD, "Device Status Tables.txt")) for c in FLASH_DOC]
    out = _flash_run(["BV50 User Manual.txt:x:44"], "bv50 flashed 4 times",
                     chunks=renamed + BV50)
    assert not _src(out, "flash_code_document")


NVM, NVP, NVT = "NV200 Range User Manual.txt", "NV200S Manual.pdf", "NV200 Spectral Manual.pdf"
NV_NAMES = {"nv200s": ["NV200S", "NV200 Spectral", "NV200"]}


def _s(src, n, section, body):
    return dict(_c(src, n, 1, section, body), product="nv200s")


NV200 = [
    _s("NV200S Quick Guide.txt", 1, "", "The NV200S is a note validator."),
    _s(NVM, 46, "NV200 Range Service Guide",
       "Flashes | Indicated Status / Error | Recommended Action\n"
       "3 | 1 | Firmware Checksum Error | Retry the download\n"
       "4 | Dataset Checksum Error\n"
       "4 | 1 | Power Supply too Low | Check the voltage"),
    _s(NVM, 47, "NV200 Range Service Guide",
       "Flashes | Indicated Status / Error | Recommended Action\n"
       "2 | Power Supply too High | Check the voltage varies under 10%\n"
       "3 | Card Format | Format the card\n4 | Payout Reset | Wait"),
    _s(NVP, 144, "Bezel/Status LED Flash Codes",
       "The bezel is flashing red and blue in a sequence.\n"
       "Flashes |  | Indicated Status / Error | Recommended Action\nRed | Blue |  |\n"
       "1 | 1 | Note Path Open | Close the lid\n1 | 2 | Note Path Jam | Clear it\n"
       "2 | 1 | Cashbox Removed | Insert it\n2 | 2 | Cashbox Jam | Clear it"),
    _s(NVT, 213, "User Manual NV200 Spectral Range \u203a 8.2.3 Ticket Module Flash Codes",
       "The SMART Ticket button can flash error codes, red and blue.\n"
       "Flashes |  | Indicated Error\n1 | 1 | No Connection\n1 | 2 | Initialisation Fail\n"
       "2 | 2 | Tab not found\n4 | 2 | Ticket Jam"),
]


def _nv(query, budget=100000):
    by = {c["id"]: c for c in NV200}
    rdb._flash_docs_cache.update(key=None, docs={})
    with patch.object(rdb, "_catalog_names", lambda: NV_NAMES), \
         patch.object(rdb, "is_excluded", lambda s: False):
        return rdb._flash_tables([dict(NV200[0])], query, NV200, by, budget=budget)


def test_the_table_holding_the_asked_pair_comes_first_other_module_last():
    out = _nv("nv200s flashing red 4x blue 2x, whats that")
    assert _src(out, "flash_table") == [(NVM, "47"), (NVP, "144"), (NVM, "46"), (NVT, "213")]
    # under a budget for two tables the asked row is kept, the Ticket Module's dropped
    n = len(NV200[2]["text"]) + len(NV200[3]["text"])
    assert _src(_nv("nv200s flashing red 4x blue 2x, whats that", n),
                "flash_table") == [(NVM, "47"), (NVP, "144")]


def test_a_module_the_question_names_is_not_demoted():
    out = _nv("nv200s ticket module flashing red 4 blue 2")
    assert _src(out, "flash_table")[0] == (NVT, "213")


def test_count_pairs_carry_the_first_count_down():
    assert rdb._count_pairs("2 | High | x\n3 | Card Format | y",
                            "4 | 1 | Low | z") == {(4, 2), (4, 3)}
    assert rdb._count_pairs("4 | 1 | Low\n| 2 | High") == {(4, 1), (4, 2)}
    assert rdb._count_pairs("Flashes | Interface\n4 | IF3\n5 | MDB") == set()


def test_other_module_reads_only_a_flash_code_title():
    own = {"nv200s", "nv200", "spectral"}
    q = {"nv200s", "flashing", "red"}
    sec = "User Manual NV200 Spectral Range \u203a 8.2.3 Ticket Module Flash Codes"
    assert rdb._other_module(sec, q, own)
    assert not rdb._other_module(sec, q | {"ticket"}, own)
    for s in ("Bezel/Status LED Flash Codes", "8.1 Bezel/Status LED Flash Codes",
              "NV200 Range Service Guide", "",
              "3 - Monitor bezel led and check flash codes on table below"):
        assert not rdb._other_module(s, q, own), s
