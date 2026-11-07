"""Family cards draw only on documents a visitor may be given (D-20261106-07).
# run-in-own-process
"""
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PATH = os.path.join(tempfile.mkdtemp(), "fam.json")
os.environ["FAMILIES_CONFIG"] = _PATH

PROMPTS = []
ROWS = {
    "p1": [{"source": "Secret API.pdf", "product": "p1", "text": "HIDDENTEXT secret register map"},
           {"source": "P1 Manual.pdf", "product": "p1", "text": "The P1 has a blue bezel."}],
    "p2": [{"source": "P2 Manual.pdf", "product": "p2", "text": "The P2 has a red bezel."}],
}
INDEX = [{"source": "Secret API.pdf", "products": ["p1"]}, {"source": "P1 Manual.pdf", "products": ["p1"]},
         {"source": "P2 Manual.pdf", "products": ["p2"]}, {"source": "Old Manual.pdf", "products": ["p2"]}]


def _mod(name, **kw):
    m = types.ModuleType(name)
    m.__dict__.update(kw)
    sys.modules[name] = m
    return m


def _gen(role, prompt, **kw):
    PROMPTS.append(prompt)
    if "yes/no" in prompt:
        return {"text": "[]"}
    return {"text": '{"Widget One": "blue bezel", "Widget Two": "red bezel"}'}


_mod("retrieval_db", is_excluded=lambda s: "secret" in (s or "").lower(),
     retrieve_from_db=lambda q, top_k=4, scope=None: list(ROWS[scope["product"]]))
_mod("llm", generate_with_fallback=_gen)
_mod("docindex", source_index=lambda: INDEX)
_mod("figures", figures_for=lambda s: {"p1_1.png": {
    "title": "Photo of the Widget One" if "Secret" in s else "Photo", "page": 1}})
cat = _mod("catalog", BLOCKED=set(), blocked_products=lambda: set(cat.BLOCKED),
           obsolete_of=lambda k: None,
           catalog=lambda: {"categories": [{"key": "c", "products": [
               {"key": "p1", "name": "Widget One"}, {"key": "p2", "name": "Widget Two"}]}]})

import families as F  # noqa: E402
import visible  # noqa: E402


def _seed(m1="", m2="", **extra):
    fam = {"key": "w", "name": "Widget", "aliases": [], "members": [
        {"name": "Widget One", "aliases": [], "product": "p1", "features": m1},
        {"name": "Widget Two", "aliases": [], "product": "p2", "features": m2}]}
    fam.update(extra)
    F.save({"families": [fam], "dismissed": [], "seen_products": ["p1", "p2"]})


def _fam():
    return F.load()["families"][0]


def test_hidden_text_never_reaches_the_prompt_or_the_card():
    _seed()
    PROMPTS.clear()
    F.refresh_auto()
    assert not any("HIDDENTEXT" in p for p in PROMPTS)
    assert any("blue bezel" in p for p in PROMPTS)
    fam = _fam()
    assert not any("HIDDENTEXT" in m["features"] for m in fam["members"])
    assert all("Secret" not in (F.picture_url(m) or "") for m in fam["members"])
    assert all("Secret" not in (o["picture"] or "") for o in F.options(fam, [0, 1], "q"))


def test_pick_picture_never_returns_an_excluded_source():
    _seed()
    fam = _fam()
    for m in fam["members"]:
        p = F._pick_picture(fam, m, set())
        assert p is None or "Secret" not in p["source"]
    assert F._pick_picture(fam, fam["members"][0], set())["source"] == "P1 Manual.pdf"


def test_a_saved_hidden_picture_is_not_shown():
    assert F.picture_url({"picture": {"source": "Secret API.pdf", "name": "p1_1.png"}}) is None
    assert F.picture_url({"picture": {"source": "P1 Manual.pdf", "name": "p1_1.png"}}) \
        == "/figure/P1%20Manual.pdf/p1_1.png"


def test_blocked_product_gives_no_picture_and_no_text():
    _seed()
    fam = _fam()
    cat.BLOCKED = {"p2"}
    try:
        assert F._pick_picture(fam, fam["members"][1], set()) is None
        assert F.pictures_for("p2") == []
        PROMPTS.clear()
        F._draft_features(fam, [1])
        assert PROMPTS and not any("red bezel" in p for p in PROMPTS)
        assert visible.may_show("Old Manual.pdf", "p2,p1", {"p2"}) is True
    finally:
        cat.BLOCKED = set()


def test_old_drafts_are_redrafted_once():
    _seed("OLD", "ADMIN", questions=[{"q": "x?", "yes": [], "auto": True}])
    d = F.load()
    d["families"][0]["members"][0]["features_auto"] = True
    F.save(d)
    F.refresh_auto()
    d = F.load()
    ms = d["families"][0]["members"]
    assert ms[0]["features"] == "blue bezel" and ms[1]["features"] == "ADMIN"
    assert d["drafted_visible_only"] is True
    ms[0]["features"] = "NEW"
    ms[0]["features_auto"] = True
    F.save(d)
    F.refresh_auto()
    assert F.load()["families"][0]["members"][0]["features"] == "NEW"


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
    print("PASS family hidden docs")
