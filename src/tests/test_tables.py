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


# The NV200S bezel table (p.83) as indexed: a spanning "Flashes" label over
# Red | Blue. "1 | 3" was answered as "red 3 flashes" on 2026-09-25.
NV200S_P83 = """[NV200S Range User Manual-v1 — Bezel/Status LED Flash Codes]
the NV200 Spectral front bezel will flash in a particular sequence:
Flashes |  | Indicated Status / Error | Recommended Action
Red | Blue |  |
1 | 1 | Note Path Open | Close the lid
1 | 2 | Note Path Jam | Power down the NV200 Spectral
| 3 | Unit Not Initialised | Return to your nearest repair centre
2 | 1 | Cashbox Removed | Insert the cashbox."""


def test_a_two_row_header_flash_table_is_read_row_by_row():
    out = tables.spell_out(NV200S_P83)
    assert tables.READ_MARK in out
    assert "Red Flashes 1 and Blue Flashes 3: Unit Not Initialised" in out
    assert "Red Flashes 1 and Blue Flashes 1: Note Path Open" in out
    assert "Red Flashes 2 and Blue Flashes 1: Cashbox Removed" in out
    # the merged cell is filled from the data row above, never from "Red | Blue"
    assert "1 | 3 | Unit Not Initialised" in out
    assert "Red | 3" not in out
    assert tables.spell_out(out) == out


def test_a_single_header_flash_table_is_read_out_too():
    out = tables.spell_out(BV30_P31)
    assert "Red Flashes 1 and Blue Flashes 3: Unit Not Initialised" in out
    assert "Red Flashes 3 and Blue Flashes 1: Firmware Checksum" in out
    assert tables.spell_out(out) == out


def test_a_continuation_borrows_labels_and_merged_cells_from_the_chunk_above():
    above = BV30_P31 .replace("\nBV30 User Manual – 31", "")
    out = tables.spell_out(BV30_P32, above=above)
    assert "3 | 2 | Interface Checksum" in out
    assert "4 | 2 | PSU Voltage too High" in out
    assert "Red Flashes 3 and Blue Flashes 2: Interface Checksum" in out
    # without the chunk above, nothing is guessed
    alone = tables.spell_out(BV30_P32)
    assert "3 | 2 | Interface Checksum" not in alone
    assert tables.READ_MARK not in alone


def test_count_columns_after_a_state_column_are_read_out():
    """S30, N4-21: NV4000 conveyor, green is a state and red/orange count."""
    t = ("[NV4000 Range User Manual-v2 — Conveyor Module]\n"
         "Green LED | Red LED | Orange LED | Description of Error\n"
         "Solid |  |  | Device in Service\n"
         "Flashing | 1 | 2 | Belt Missing\n"
         "Flashing | 2 | 1 | Belt failed to move\n"
         "Flashing | 2 | 2-4 | Internal Comms")
    out = tables.spell_out(t)
    assert "Green LED Flashing, Red LED 1 and Orange LED 2: Belt Missing" in out
    assert "Green LED Flashing, Red LED 2 and Orange LED 1: Belt failed to move" in out
    assert "Internal Comms" not in out.split(tables.READ_MARK)[1]
    assert tables.spell_out(out) == out


def test_non_flash_two_column_tables_get_no_readout():
    t = "[Doc — Pins]\nPin | Signal | Direction\n1 | 12V | In\n2 | 0V | In"
    assert tables.READ_MARK not in tables.spell_out(t)
    t = "[Doc — Specs]\nMinimum | Maximum | Unit\n10 | 20 | V\n1 | 2 | A"
    assert tables.READ_MARK not in tables.spell_out(t)


def test_phrase_runs_are_three_content_words_in_order():
    import retrieval_db
    runs = retrieval_db._phrase_runs("What's the payout module capacity on the NV200 Spectral?")
    assert ["payout", "module", "capacity"] in runs
    assert retrieval_db._phrase_runs("how do I reset it") == []


# D-20261026-04: the shared "ITL Device Flash Codes" document, chunks 11-15
# copied verbatim from the live index (ITL Device Flash Codes.txt:
# fc7c283dde4d353c:<n>). Its count cells read "4x", its header's second
# cell is "Description" (not blank), and its product line is plain text,
# often in the chunk before.
FC11 = '[ITL Device Flash Codes]\nUnknown Cause\n| GREEN | Flashing 1Hz | None\nCBA9\nStatus (Flashes) | Description\n1x | Note Float Error\n2x | Note Jam\n3x | Checksum Error\n4x | PSU Error\nNV9 Spectral, NV9USB+ Range and NV10USB+ Range\nLED Colour Status (Flashes) | Description | Recommended Action\nLong | Short\n0x | 1x | NV9USB Range and NV9 Spectral Range: Recycler updating the firmware (recycler and validator flashing alternatively) | Wait for the process to finish, the device will reboot after it is completed\n1x | 1x | NV9USB Range and NV9 Spectral Range: Note Path Open NV10 USB Range: not in use | Ensure upper assembling is closed\n2x | Path jam | Clear the jam in the note path\n3x | Unit not Initialised | Contact ITL technical support\n4x | NV9USB Range: Rear Sensor Covered NV9 Spectral Range: Not in Use NV10USB Range: Internal Sensor Error | Contact ITL technical support\n2x | 1x | NV9 Spectral only (FW≥1.20 and plastic cashbox): Cashbox removed | Insert the cashbox\n2x | Cashbox jam | Check stacking mechanism\n3x | NV9 Spectral only (FW≥1.21): recycler not detected | Check the connection to the recycler Send poll or reboot the unit\n3x | 1x | Firmware Checksum Error | Reprogram the device to the latest available version\n2x | Interface Checksum Error\n3x | EEPROM Checksum Error\n4x | Dataset Checksum Error'
FC12 = '[ITL Device Flash Codes]\npoll or reboot the unit\n3x | 1x | Firmware Checksum Error | Reprogram the device to the latest available version\n2x | Interface Checksum Error\n3x | EEPROM Checksum Error\n4x | Dataset Checksum Error\nLED Colour Status (Flashes) | Description | Recommended Action\n5x | NV9 Spectral: with build revision 7.1 connected to Multi Note Float is not compatible NV9 Spectral: Note Float support is disabled using firmware ≥ 1.21 NV9 Spectral: Add-on not compatible | The Multi Note Float is not supported on the NV9 Spectral build revision 7.0 only from firmware ≥ 1.15 All other build revisions are supported Note Float support is disabled with NV9S firmware ≥ 1.21 Top reader only. Not compatible with recycler firmware\n7x | NV9 Spectral Range with software Error | Contact ITL technical support\n4x | 1x | Power Supply to Low | Check power supply\n2x | Power Supply to High\nMulti Note Float (NV22S)\nOperational Flash Codes\nLed State | Status\nRecycler and validator flashing alternatively - rapid flashing | Updating the recycler firmware\nRapid flashing | Starting up\nOn | Idle\nPulse every 3 seconds | Disabled\nFlash until cycle is finished - rapid flashing | Paying out\nError Flash Codes'
FC13 = '[ITL Device Flash Codes]\nLed State | Status\nrapid flashing | Updating the recycler firmware\nRapid flashing | Starting up\nOn | Idle\nPulse every 3 seconds | Disabled\nFlash until cycle is finished - rapid flashing | Paying out\nError Flash Codes\nLED Colour Status (Flashes) | Description\nLong | Short\n0x | 1x | Invalid currency\n3x | 0x | Recovery Mode\n1x | Note Jam\n2x | Sensor Fault\n3x | Tape Fault\n4x | Diverter Fault\n5x | Note Memory is Corrupt\nNote Float (NV11, NV11S and CBA11)\nLED Colour | Status (Flashes) | Description\nn/a | No Flash | Operating normally\nRed | Flashing 1Hz | Note Transport Error in Note Float\nRed | 2x | Software Error\nRed | 3x | Calibration Error\nRed | 4x | Diverter Error\nRed | 5x | Motor Timeout\nBV Range (BV20, BV30, BV50 and BV100)'
FC14 = '[ITL Device Flash Codes]\nLED Colour | Status (Flashes) | Description\nRed | Flashing 1Hz | Note Transport Error in Note Float\nRed | 2x | Software Error\nRed | 3x | Calibration Error\nRed | 4x | Diverter Error\nRed | 5x | Motor Timeout\nBV Range (BV20, BV30, BV50 and BV100)\nLED Colour Status (Flashes) | Description | Recommended Action\nLong | Short |\n1x | 1x | BV20, BV30: Not in Use BV50, BV100: Note Path Open | BV50, BV100: Ensure upper assembling is closed.\n2x | Note Path Jam | BV50, BV100: With the unit open, check the bill paths for and jams or debris that may be blocking the path or sensors.\n3x | Unit not Initialised | Contact ITL technical support\n4x | BV20, BV30: Not in Use BV50, BV100: Cashbox Jam | BV20, BV30: Not in Use BV100: Flip the unit over and check the cash box area and note path rollers for any jams or debris then reassemble the unit.\n2x | 1x | BV20, BV30: Not in Use BV50, BV100: Cashbox Removed | BV20, BV30: Not in Use BV50, BV100: Insert the cashbox.\n2x | BV20, BV30: Not in Use BV50, BV100: Cashbox Jam | BV20, BV30: Not in Use BV50: Flip the unit over and check the cash box area and note path rollers for any jams or debris then reassemble the unit.\n3x | 1x | Firmware Checksum Error | Reprogram the device to the latest available version\n2x | Interface Checksum Error\n3x | EEPROM Checksum Error\n4x | Dataset Checksum Error\n4x | 1x | Power Supply to Low | Check power supply\n2x | Power Supply to high'
FC15 = '[ITL Device Flash Codes]\nto the latest available version\n2x | Interface Checksum Error\n3x | EEPROM Checksum Error\n4x | Dataset Checksum Error\n4x | 1x | Power Supply to Low | Check power supply\n2x | Power Supply to high DA3\nLED Colour status (Flashes) | Description\nLong | Short\n1x | 1x | Incompatible Dataset\n2x | No Validator Connection Found\n3x | No Valid Download Files Found\n4x | Download Fail\n5x | Memory Card Fail\n6x | Not in Use\n7x | Not in Use\n8x | Heap Fail (Data Logging Problem)\nCR-158 (Coupon Printer)\nLED Colour status (Flashes) | Description\nRed | Yellow\n1x | 1x | Not in Use\n2x | Initialisation Fail\n3x | No Print Head\n4x | Not in Use\n2x | 1x | No Paper\n2x | Not in Use\n3x | Load Fail\n3x | 1x | Not in Use\n2x | Not in Use\n3x | Not in use\n4x | Cut Fail\n5x | Unknown Jam\n4x | 1x | Unknown Error'


def _readout(text, above):
    return tables.spell_out(text, above=above)


def test_bv_long_count_is_carried_to_its_short_rows():
    """C20261026-03: "4 long 2 short" was answered with the 1-long-4-short
    row (Cashbox Jam). The long count is written once per group."""
    out = _readout(FC14, FC13)
    assert "Long flashes 4 and Short flashes 2: Power Supply to high" in out
    assert ("Long flashes 1 and Short flashes 4: BV20, BV30: Not in Use "
            "BV50, BV100: Cashbox Jam") in out
    assert not any("Long flashes 4" in ln and "Cashbox" in ln
                   for ln in out.split(chr(10)))
    assert 'Read cell by cell under "BV Range (BV20, BV30, BV50 and BV100)":' in out


def test_nv22s_rows_are_read_under_the_heading_in_the_chunk_before():
    """C20261026-36: the NV22S heading is in chunk 12, its rows in 13."""
    out = _readout(FC13, FC12)
    assert 'Read cell by cell under "Multi Note Float (NV22S)":' in out
    assert "Long flashes 3 and Short flashes 0: Recovery Mode" in out
    assert "Long flashes 3 and Short flashes 1: Note Jam" in out


def test_a_continuation_with_an_overlap_fragment_keeps_its_table():
    """Chunk 12 opens with the cut tail of a line ("poll or reboot the
    unit") and has neither its header nor its heading."""
    out = _readout(FC12, FC11)
    assert 'Read cell by cell under "NV9 Spectral, NV9USB+ Range and NV10USB+ Range":' in out
    assert "Long flashes 4 and Short flashes 1: Power Supply to Low" in out
    assert "Long flashes 3 and Short flashes 2: Interface Checksum Error" in out


def test_a_table_with_no_product_line_gets_no_heading():
    """DA3 is glued onto the last BV row: the DA3 table has no heading of
    its own, and must never inherit "BV Range"."""
    out = _readout(FC15, FC14)
    da3 = out.split("Long flashes 1 and Short flashes 1: Incompatible Dataset")[0]
    assert da3.rstrip().endswith(tables.READ_MARK)
    # the overlap rows keep their BV reading (3 long), never 4 long from
    # the end of the chunk above
    assert "Long flashes 3 and Short flashes 2: Interface Checksum Error" in out
    assert "Long flashes 4 and Short flashes 2: Interface Checksum Error" not in out
    assert 'Read cell by cell under "CR-158 (Coupon Printer)":' in out
    assert "Red flashes 2 and Yellow flashes 1: No Paper" in out


def test_count_table_read_out_is_idempotent():
    for text, above in ((FC14, FC13), (FC13, FC12), (FC12, FC11), (FC15, FC14)):
        once = _readout(text, above)
        assert once != text
        assert _readout(once, above) == once
        # nothing in the document text is changed, only read-outs appended
        assert once.startswith(text)


def test_a_spelled_chunk_above_still_carries_its_table():
    """At ingest, spell_out runs without the chunk above, so a stored chunk
    can end in read-out lines; they must not end the table it continues."""
    assert "Long flashes 4 and Short flashes 1: Power Supply to Low" in \
        _readout(FC12, tables.spell_out(FC11))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
