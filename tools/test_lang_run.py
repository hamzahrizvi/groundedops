"""Self-check for tools/lang_run.py: python tools/test_lang_run.py (no model, no network)."""
import contextlib, io, json, os, sys, tempfile
tmp = tempfile.mkdtemp()
os.environ["LAB_OPS"] = os.path.join(tmp, "ops")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lang_run as L


def W(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(o, open(p, "w", encoding="utf-8"), ensure_ascii=False)


src = os.path.join(tmp, "runs", "r1")
cases = [{"id": "x1", "q": "How do I reset it?", "type": "lookup", "expected": "answered"},
         {"id": "x2", "q": "Does it fly?", "type": "phantom", "expected": "rejected"}]
W(os.path.join(src, "cases.json"), {"cases": cases})
W(os.path.join(src, "manuals", "a.txt"), "x")
W(os.path.join(src, "cases_state.json"), {"summary": {"sys_normal": {"pct_of_max": 90.0}}})

assert L.intact({"m.pdf": [3]}, {"m.pdf": [5]}) is False
assert L.intact({"m.pdf": []}, {"m.pdf": []}) is True
assert L.intact({"m.pdf": [3]}, {"m.pdf": [3, 4]}) is True

try:
    L.prep(src, os.path.join(tmp, "elsewhere"), ["en"])
    raise AssertionError("prep accepted a folder outside heldout")
except SystemExit:
    pass
out = os.path.join(tmp, "ops", "heldout", "lang")
L.prep(src, out, ["en"])
en = L.folder(out, src, "en")
assert json.load(open(os.path.join(en, "cases.json")))["cases"] == cases
assert os.path.exists(os.path.join(en, "manuals", "a.txt"))

EN = "To reset the unit, hold the power button for ten seconds and then release it until the light blinks."
EN2 = "The manual does not say that this product can fly, so I cannot confirm that for you."
FR = "Pour réinitialiser l'appareil, maintenez le bouton d'alimentation enfoncé pendant dix secondes puis relâchez-le."
FR2 = "Le manuel ne précise pas que ce produit peut voler, donc je ne peux pas vous le confirmer."


def state(a1, a2, s1, s2):
    row = lambda a, s: {"answer": a, "wall": 1.0, "usd": 0.001, "sources": s}
    g = lambda v: {"sys_normal": {"score": 3, "verdict": v}}
    return {"sys_normal": {"x1": row(a1, s1), "x2": row(a2, s2)},
            "grades": {"x1": g("correct"), "x2": g("correct_refusal")}, "summary": {"grader": {"usd": 0.01}}}


fr = L.folder(out, src, "fr")
W(os.path.join(en, "cases_state.json"), state(EN, EN2, {"m.pdf": [3]}, {}))
W(os.path.join(fr, "cases.json"), {"cases": cases})
W(os.path.join(fr, "cases_state.json"), state(FR, FR2, {"m.pdf": [3, 4]}, {}))
with contextlib.redirect_stdout(io.StringIO()):
    r = L.report(src, out, ["en", "fr"])
f = r["fr"]
assert f["answers_in_lang"] == "2/2", f
assert f["citations_intact"] == "1/1" and f["citations_dropped"] == 0, f
assert f["pct_of_en_fresh"] == 100.0, f
assert "fr" in r["at_or_above_90_of_ref"] and "fr" in r["at_or_above_90_of_fresh"], r
print("ok")
