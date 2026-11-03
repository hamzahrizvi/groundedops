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
    assert _ids(out) == ["88", "89", "12", "13"]
    assert _ids(out, "section_reference") == ["12", "13"]
    assert out[2]["retrieval_score"] == 0.0


def test_nv4000_chain_reaches_page_52_and_its_section():
    out = _run(NV4000, ["NV4000.pdf:x:98"],
               "our nv4000 bezel keeps flashing 4 red then 1 blue, PSU?")
    assert _ids(out) == ["98", "99", "100", "30", "31"]
    assert all(c["with_previous"] for c in out[1:3])
    assert _ids(out, "section_reference") == ["30", "31"]


def test_merged_first_cell_row_is_matched():
    out = _run(NV4000, ["NV4000.pdf:x:98"], "nv4000 blinking red four blue two")
    assert _ids(out, "section_reference") == ["30", "31"]


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


def test_section_cut_at_budget_in_document_order():
    by = {c["id"]: c for c in BV30}
    out = rdb._referenced_sections(
        [dict(by["BV30.pdf:x:89"])], "flashing 4 red 1 blue", BV30,
        budget=len(by["BV30.pdf:x:12"]["text"]) + 5)
    assert _ids(out, "section_reference") == ["12"]
