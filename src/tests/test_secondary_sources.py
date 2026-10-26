"""Test scripts, drawings, certificates and the like rank below the manuals (2026-10-23)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
