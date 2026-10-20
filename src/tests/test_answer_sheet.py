"""answer_sheet: figure checks and verdicts, one test per finding case."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import answer_sheet as a


def P(text, product="", section=""):
    return {"text": text, "product": product, "section": section}


def R(product, item, value, passage, **kw):
    return dict(product=product, item=item, value=value, passage=passage, **kw)


def test_C20261020_08_within_psu_rating():
    ps = {"p1": P("Meanwell RSP-320-24 +24 V DC / 13.4 A", "cashgenic"),
          "p2": P("Spectral Payout peak current 5.50 A", "cashgenic"),
          "p3": P("SMART Coin System peak 6.5 A", "cashgenic")}
    sheet = {"rows": [R("cashgenic", "payout peak", "5.50 A", "p2"),
                      R("cashgenic", "coin peak", "6.5 A", "p3"),
                      R("cashgenic", "psu rating", "13.4 A", "p1")],
             "verdicts": [{"kind": "within", "items": ["payout peak", "coin peak"],
                           "limit": "psu rating"}]}
    v = a.check(sheet, ps)["verdicts"][0]
    assert v["verdict"] == "Yes", v


def test_C20261020_11_each_mixed():
    ps = {"p1": P("The unit supports a static IP address", "mycheckr"),
          "p2": P("No static addressing on the mini", "mycheckr_mini")}
    rows = [R("mycheckr", "static ip", "static IP", "p1", answer="yes"),
            R("mycheckr_mini", "static ip", "No static", "p2", answer="no")]
    sheet = {"rows": rows, "verdicts": [{"kind": "each", "item": "static ip"}]}
    assert a.check(sheet, ps)["verdicts"][0]["verdict"] == "Mixed"
    v = a.check(dict(sheet, rows=rows[1:]), ps, products=["mycheckr", "mycheckr_mini"])["verdicts"][0]
    assert v["verdict"] is None and v["note"] == "not found for mycheckr"


def test_C20261020_24_wrong_product_passage_dropped():
    ps = {"p1": P("SSP barcode default 0xFE (barcode disabled)", "nv200s"),
          "p2": P("DIP switch 1 default OFF", "nv4000")}
    sheet = {"asks": ["barcode default"], "products": ["nv200s", "nv4000"], "rows": [
        R("nv4000", "barcode default", "0xFE", "p1"),
        R("nv200s", "other", "enabled", "p1"),
        R("nv200s", "barcode default", "0xFE", "p1"),
        R("nv4000", "dip", "OFF", "p2")]}
    out = a.check(sheet, ps)
    d = {(r["product"], r["value"]): r for r in out["dropped"]}
    assert d[("nv4000", "0xFE")]["note"] == "not found for nv4000"
    assert d[("nv200s", "enabled")]["reason"] == "value not in passage"
    assert [r["value"] for r in out["rows"]] == ["0xFE", "OFF"]
    assert out["missing"] == [{"product": "nv4000", "item": "barcode default",
                               "note": "not found for nv4000"}]


def test_C20261019_30_lower_temperature():
    ps = {"a": P("+5 °C", "nv9_spectral"), "b": P("+3°C", "nv9usb")}
    sheet = {"rows": [R("nv9_spectral", "min temp", "+5°C", "a"), R("nv9usb", "min temp", "+3C", "b")],
             "verdicts": [{"kind": "lower", "item": "min temp"}]}
    assert a.check(sheet, ps)["verdicts"][0]["verdict"] == "nv9usb"


def test_C20261019_14_conflicting_passages():
    ps = {"q": P("Relay 2 COM is Pin 7", "myconnect", "Quick Start"),
          "w": P("Relay 2 COM goes to Pin 9", "myconnect", "Wiring")}
    sheet = {"rows": [R("myconnect", "relay 2 COM pin", "Pin 7", "q"),
                      R("myconnect", "relay 2 COM pin", "Pin 9", "w")],
             "verdicts": [{"kind": "value", "item": "relay 2 COM pin"}]}
    out = a.check(sheet, ps)
    assert out["conflicts"][0]["values"] == ["Pin 7", "Pin 9"]
    assert out["verdicts"][0]["verdict"] is None and out["verdicts"][0]["note"] == "documents disagree"


def test_C20261019c_03_missing_then_lower_higher():
    ps = {"a": P("maximum current sink 0.2 A", "bv30"), "b": P("maximum current sink 0.3 A", "nv9usb"),
          "c": P("BV30 running current 0.54 A, MCBF 100,000", "bv30"),
          "d": P("NV9USB+ running current 1.0 A, MCBF 200,000", "nv9usb")}
    sheet = {"asks": ["running current", "MCBF"], "products": ["bv30", "nv9usb"],
             "rows": [R("bv30", "maximum current sink", "0.2 A", "a"),
                      R("nv9usb", "maximum current sink", "0.3 A", "b")]}
    assert len(a.check(sheet, ps)["missing"]) == 4
    sheet["rows"] += [R("bv30", "running current", "0.54 A", "c"), R("bv30", "MCBF", "100,000", "c"),
                      R("nv9usb", "running current", "1.0 A", "d"), R("nv9usb", "MCBF", "200,000", "d")]
    sheet["verdicts"] = [{"kind": "lower", "item": "running current"}, {"kind": "higher", "item": "MCBF"}]
    out = a.check(sheet, ps)
    assert out["missing"] == []
    assert [v["verdict"] for v in out["verdicts"]] == ["bv30", "nv9usb"]


def test_C20261019c_07_rms_and_peak_do_not_conflict():
    ps = {"p": P("Max Load 1.10A RMS, 1.35A peak at 12VDC", "x")}
    sheet = {"rows": [R("x", "max load", "1.10A", "p", condition="RMS"),
                      R("x", "max load", "1.35A", "p", condition="peak")],
             "verdicts": [{"kind": "value", "item": "max load", "condition": "peak"}]}
    out = a.check(sheet, ps)
    assert out["conflicts"] == [] and out["verdicts"][0]["verdict"] == "1.35A"


def test_C20261019c_37_condition_value():
    ps = {"p": P("EUR 0.01 25% of all coins, max 20 coins if no other coins in the device", "x")}
    cond = "if no other coins in the device"
    sheet = {"asks": ["share", "max coins"], "rows": [
        R("x", "share", "25%", "p"), R("x", "max coins", "20", "p", condition=cond),
        R("x", "max coins", "30", "p")],
        "verdicts": [{"kind": "value", "item": "max coins"}]}
    out = a.check(sheet, ps)
    assert out["missing"] == [] and out["verdicts"][0]["verdict"] == "20"
    assert out["dropped"][0]["reason"] == "value not in passage"


def test_C20261019c_38_each_mixed():
    ps = {"a": P("pin-to-pin replacement", "scs"), "b": P("not a replacement", "scs_twin")}
    sheet = {"rows": [R("scs", "replacement", "pin-to-pin replacement", "a", answer="yes"),
                      R("scs_twin", "replacement", "not a replacement", "b", answer="no")],
             "verdicts": [{"kind": "each", "item": "replacement"}]}
    assert a.check(sheet, ps)["verdicts"][0]["verdict"] == "Mixed"


def test_C20261019f_03_device_section():
    ps = {"pA": P("tap 5 times on the centre of the screen ... password 123456", "cashgenic", "Control Admin Menu"),
          "pB": P("click the Admin logo", "cashgenic", "Remote Admin Menu")}
    row = R("cashgenic", "open admin", "click the Admin logo", "pB", device="control")
    out = a.check({"rows": [row]}, ps)
    assert out["dropped"][0]["reason"] == "passage is about another device"
    ok = R("cashgenic", "open admin", "tap 5 times", "pA", device="control")
    pin = R("cashgenic", "pin", "123456", "pA")
    assert len(a.check({"rows": [ok, pin]}, ps)["rows"]) == 2


def test_model_output_edge_cases():
    assert a._numbers("100x50 mm") == [100.0, 50.0] and a._numbers("0x50") == []
    v = a.check({"rows": ["junk", {"value": "1"}],
                 "verdicts": ["junk", {"kind": "within", "items": "x", "limit": "y"}]}, {})
    assert v["rows"] == [] and v["verdicts"][0]["verdict"] is None
    ps = {"a": P("yes", "p"), "b": P("yes", "q")}
    rows = [R("p", "i", "yes", "a", answer="Yes."), R("q", "i", "yes", "b", answer="yes")]
    each = {"rows": rows, "verdicts": [{"kind": "each", "item": "i"}]}
    assert a.check(each, ps)["verdicts"][0]["verdict"] == "Yes"
    rows[1]["answer"] = "unclear"
    assert a.check(each, ps)["verdicts"][0]["note"] == "not found for q"


def test_parse_sheet_fallback():
    for bad in ("", "not json", "[]", '{"rows": 3}'):
        assert a.parse_sheet(bad) is None
    assert a.parse_sheet('```json\n{"rows": []}\n```') == {"rows": []}


def test_not_imported_in_answer_path():
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(src, "main.py"), encoding="utf-8") as f:
        assert "answer_sheet" not in f.read()
