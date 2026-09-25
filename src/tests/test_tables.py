"""tables.py and the retrieval hooks around it (2026-09-25).

The inputs are the real chunks that failed: the BV30 flash-code table that
continues across a page break, and the NV9USB+ long-by-short flash matrix.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tables  # noqa: E402

BV30_P31 = """[BV30 User Manual-v1 — Bezel Flash Codes]
Refer to the table below for a summary of the flash codes:
Red Flashes | Blue Flashes | Error | Recommend Action
1 | 2 | Note Path Jam | Check the note path
| 3 | Unit Not Initialised | Initialise the unit
| 4 | Sensor Covered | Check the unit for any debris
3 | 1 | Firmware Checksum | Try to reprogram the firmware
BV30 User Manual – 31"""

BV30_P32 = """[BV30 User Manual-v1]
| 2 | Interface Checksum | Try to reprogram the firmware
| 3 | EEPROM Checksum | Return to the service centre
4 | 1 | PSU Voltage too Low | Check the power supply
| 2 | PSU Voltage too High | Check the power supply"""

NV9_MATRIX = """[NV9USB+ Range User Manual-v1 — Bezel LED Error Flash Codes]
The combination and their meanings can be found in the table below:
Number of Long Flashes | Number of Short Flashes |  |  |
| 1 | 2 | 3 | 4
1 | Note Path Open | Note Path Jam | Unit not Initialised | Rear Sensor Covered
2 | N/A | Cashbox Jam | N/A | N/A
After updating the device the validator may flash alternately."""


def test_a_continuation_carries_its_heading_header_and_merged_cells():
    texts, secs = tables.carry_table_context(
        [BV30_P31, BV30_P32], ["Bezel Flash Codes", ""], "BV30 User Manual-v1")
    cont = texts[1]
    assert secs[1] == "Bezel Flash Codes"
    assert cont.startswith("[BV30 User Manual-v1 — Bezel Flash Codes]")
    assert "Red Flashes | Blue Flashes | Error | Recommend Action" in cont
    # "| 2 | Interface Checksum" continues red-flash group 3 from page 31.
    assert "3 | 2 | Interface Checksum" in cont
    assert "4 | 2 | PSU Voltage too High" in tables.spell_out(cont)


def test_merged_cells_are_filled_within_a_chunk():
    out = tables.spell_out(BV30_P31)
    assert "1 | 3 | Unit Not Initialised" in out
    assert "1 | 4 | Sensor Covered" in out
    assert "3 | 1 | Firmware Checksum" in out


def test_a_matrix_is_read_cell_by_cell():
    out = tables.spell_out(NV9_MATRIX)
    assert tables.READ_MARK in out
    assert "Number of Long Flashes 1 and Number of Short Flashes 2: Note Path Jam" in out
    assert "Number of Long Flashes 1 and Number of Short Flashes 1: Note Path Open" in out
    assert "Number of Long Flashes 2 and Number of Short Flashes 2: Cashbox Jam" in out
    assert "N/A" not in out.split(tables.READ_MARK)[1]
    # The column-label row is never filled down.
    assert "| 1 | 2 | 3 | 4" in out


def test_spell_out_and_carry_are_idempotent():
    for t in (BV30_P31, BV30_P32, NV9_MATRIX):
        once = tables.spell_out(t)
        assert tables.spell_out(once) == once
    texts, secs = tables.carry_table_context(
        [BV30_P31, BV30_P32], ["Bezel Flash Codes", ""], "BV30 User Manual-v1")
    again, secs2 = tables.carry_table_context(texts, secs, "BV30 User Manual-v1")
    assert again == texts and secs2 == secs


def test_a_table_followed_by_prose_is_not_continued():
    prev = "[Doc — Specs]\nA | B | C\n1 | 2 | 3\nThis table has finished.\nMore prose here."
    nxt = "[Doc]\n| 5 | 6\n7 | 8 | 9"
    texts, secs = tables.carry_table_context([prev, nxt], ["Specs", ""], "Doc")
    assert texts[1] == nxt and secs[1] == ""


def test_plain_text_and_simple_tables_are_untouched():
    for t in ("No tables here at all.",
              "[Doc — Weights]\nUnit | Weight\nNV9 | 1.05 Kg\nBezel | 0.10 Kg"):
        assert tables.spell_out(t) == t


def test_phrase_runs_are_three_content_words_in_order():
    import retrieval_db
    runs = retrieval_db._phrase_runs("What's the payout module capacity on the NV200 Spectral?")
    assert ["payout", "module", "capacity"] in runs
    assert retrieval_db._phrase_runs("how do I reset it") == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
