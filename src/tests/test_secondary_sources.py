"""Test scripts, drawings, certificates and the like rank below the manuals (2026-10-23)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DOC_KINDS_PATH", os.path.join(tempfile.gettempdir(), "go-test-doc-kinds-absent.json"))

import retrieval_db as r  # noqa: E402

SECONDARY = ["NV4000 Integration Test Script-v1.pdf", "MyCheckr Test Script -  Power Conditions.txt",
             "CN00392_2-29SEP2023 (att645529660).pdf", "CN392_1 Power & USB Comms Cable.pdf",
             "WR02040_4.pdf", "PA01083 - NV10USB+ Lower Housing Assembly.pdf", "GA00959.pdf",
             "MC02186_1.pdf", "E361421-20210722-CertificateofCompliance.pdf", "DK-130480-UL.pdf",
             "EU Declaration of Conformity RoHS NV9 02DEC2024 GA02312_1.pdf", "SD-15-UK-dec.pdf",
             "BV50 Intertek report 1003799779CHI-001a.pdf", "NV4000 Range Product Change Notifications.txt",
             "NV4000 Firmware Release Notes.txt", "NV200 USD01G32 Software Release Documentation.pdf"]
PRIMARY = ["BV30 User Manual.txt", "NV4000 Range User Manual-v2.pdf", "NV200 Spectral SSP Manual v.1.pdf",
           "NV10USB+ User Manual.txt", "NV9USB+ Range Validator Update Guide.txt", "ITL Device Flash Codes.txt",
           "Why am I not getting correct age estimates from my MyCheckr Mini-.txt", "nv9usb-config-card.pdf"]


def test_document_kinds():
    assert [s for s in SECONDARY if not r.is_secondary(s)] == []
    assert [s for s in PRIMARY if r.is_secondary(s)] == []


def test_a_manual_outranks_a_test_script_but_keeps_its_score():
    ts = {"source": SECONDARY[0], "rerank_score": 0.99}
    man = {"source": PRIMARY[0], "rerank_score": 0.6}
    weak = {"source": PRIMARY[1], "rerank_score": 0.1}
    assert sorted([weak, ts, man], key=r.rank_key, reverse=True) == [man, ts, weak]
    assert ts["rerank_score"] == 0.99


def test_a_stored_kind_shelves_release_notes_and_certificates_only():
    was = (r._KINDS_PATH, r._kinds)
    try:
        with tempfile.TemporaryDirectory() as d:
            r._KINDS_PATH, r._kinds = os.path.join(d, "doc_kinds.json"), None
            notes, script = "Firmware 4.1.txt", "NV4000 Integration Test Script-v1.pdf"
            named_pcn = "NV4000 Range Product Change Notifications.txt"
            r.set_kinds({notes: "release_note", "UL listing.txt": "certificate",
                         "NV200 Cashbox, Chassis and Docking Plate.txt": "change_notice", named_pcn: "change_notice",
                         "Power Conditions.txt": "test_script", "Smart Payout Final Assembly.txt": "drawing",
                         script: "manual"})
            assert r.is_secondary(notes) and r.is_secondary("UL listing.txt")
            # a stored change notice, script or drawing does not move a page (nv200s-02: PCN pages hold part numbers)
            assert not any(r.is_secondary(n) for n in ("NV200 Cashbox, Chassis and Docking Plate.txt",
                                                        "Power Conditions.txt", "Smart Payout Final Assembly.txt"))
            assert r.is_secondary(named_pcn)     # its name still shelves it, as on main
            assert r.is_secondary(script)        # a stored kind never lifts a page the name rule shelves
            assert r.is_secondary("WR02040_4.pdf") and not r.is_secondary("BV30 User Manual.txt")
            r._kinds = None                      # re-read from the file
            assert r.is_secondary(notes)
            man, rn = {"source": "BV30 User Manual.txt", "rerank_score": 0.6}, {"source": notes, "rerank_score": 0.99}
            assert sorted([rn, man], key=r.rank_key, reverse=True) == [man, rn]
            open(r._KINDS_PATH, "w").write("{not json")
            r._kinds = None                      # unreadable: the name rule
            assert not r.is_secondary(notes) and r.is_secondary("WR02040_4.pdf")
    finally:
        r._KINDS_PATH, r._kinds = was


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
