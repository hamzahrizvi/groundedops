"""Product families: look-alike products customers mix up (CEO 2026-11-05).

A customer who writes "smart hopper" may have a SMART Hopper 3 or a SMART
Hopper 4, and the answers differ; "ICU" may be an ICU Lite or an ICU Pro.
The catalogue could not say so: "SMART Hopper" was an alias of the SMART
Coin System, so every "smart hopper" question was answered from the SH4's
manuals -- even in a chat scoped to the Smart Hopper 3 (lab, 2026-11-05).

A family is the name customers use ("SMART Hopper") and its members, each
with the catalogue product whose documents answer for it (members may share
one: ICU Lite and ICU Pro are both filed under "icu"), what tells it apart,
and a picture. When a question names the family but no member, the reply
asks which one, with a card per member; "not sure" gets the differences.
Composed here: no model call on the chat path.

Kept in product_families.json beside the catalogue, edited in the console's
Products page. refresh_auto() runs after an upload: it groups new products
whose names share a model code, picks pictures from the figure sidecars and
drafts missing "how to tell" text from the manuals. A field an admin typed
is never overwritten.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from urllib.parse import quote

import jsonstore

logger = logging.getLogger(__name__)

_PATH = os.getenv("FAMILIES_CONFIG", "product_families.json")
_refreshing = threading.Lock()

# Our own reply says this, so the next turn knows it is the answer to it
# (main._CLARIFY_MARKERS, and resolve() below).
MARKER = "pick yours below"
# The "general question" button's text, so it reads as every member.
GENERAL = "general question for every"

_SH_FEEDER = ("If a coin feeder sits on top of the hopper, it is a SMART "
              "Hopper 4 fitted as a SMART Coin System.")

# The CEO's list (2026-11-05). Features only where the CEO or the SH3->SH4
# replacement document states them; the rest is drafted from the manuals by
# refresh_auto() and shown in the console for checking.
_SEED = {"families": [
    {"key": "smart_hopper", "name": "SMART Hopper",
     "aliases": ["smart hopper", "smart hoppers"],
     "questions": [
         {"q": "Is there an SD card slot on the front of the hopper?", "yes": ["SMART Hopper 4"]},
         {"q": "Does a coin feeder sit on top of the hopper?", "yes": ["SMART Hopper 4"]}],
     "members": [
         {"name": "SMART Hopper 4", "aliases": ["SH4", "Smart Hopper 4", "hopper 4"],
          "product": "sku_scs",
          "features": "Has an SD card slot on the front. The configuration "
                      "button is pressed and held for 5 seconds and switches "
                      "between SSP and CC2 only. " + _SH_FEEDER},
         {"name": "SMART Hopper 3", "aliases": ["SH3", "Smart Hopper 3", "hopper 3"],
          "product": "smart_hopper_3",
          "features": "No SD card slot on the front. The configuration button "
                      "cycles between SSP, CC2 and CC1."}]},
    {"key": "scs", "name": "SMART Coin System",
     "aliases": ["SCS", "coin system"],
     "questions": [
         {"q": "Are there two hoppers in it, side by side?", "yes": ["Twin SMART Coin System"]}],
     "members": [
         {"name": "SMART Coin System", "aliases": ["single SCS"],
          "product": "sku_scs", "features": ""},
         {"name": "Twin SMART Coin System", "aliases": ["Twin SCS", "TSCS"],
          "product": "sku_scs", "features": ""}]},
    {"key": "nv200", "name": "NV200", "aliases": [],
     "questions": [
         {"q": "Is the validator fitted to a payout module that stores notes and pays them out?",
          "yes": ["SMART Payout", "Spectral Payout"]},
         {"q": "Does its label say Spectral?", "yes": ["NV200 Spectral", "Spectral Payout"]}],
     "members": [
         {"name": "NV200", "aliases": [], "product": "nv200s",
          "features": "The note validator on its own, with no payout module."},
         {"name": "SMART Payout", "aliases": ["NV200 Payout", "Smart Payout"],
          "product": "nv200s",
          "features": "An NV200 fitted to a payout module, which stores notes "
                      "and pays them out."},
         {"name": "NV200 Spectral", "aliases": ["NV200S"], "product": "nv200s",
          "features": "The NV200 Spectral note validator on its own, with no "
                      "payout module."},
         {"name": "Spectral Payout", "aliases": ["NV200 Spectral Payout"],
          "product": "nv200s",
          "features": "An NV200 Spectral fitted to a payout module."}]},
    {"key": "nv9", "name": "NV9", "aliases": [],
     "questions": [
         {"q": "Is a note float (a round drum that stores notes) fitted on top?",
          "yes": ["NV11", "NV11 Spectral"]},
         {"q": "Is a multi-note float fitted on top?", "yes": ["NV22 Spectral"]},
         {"q": "Does its label say Spectral?",
          "yes": ["NV9 Spectral", "NV11 Spectral", "NV22 Spectral"]}],
     "members": [
         {"name": "NV9USB+", "aliases": ["NV9 USB", "NV9USB", "NV9 USB+"],
          "product": "nv9usb",
          "features": "The NV9USB+ note validator on its own."},
         {"name": "NV9 Spectral", "aliases": ["NV9S"], "product": "nv9_spectral",
          "features": "The NV9 Spectral note validator on its own."},
         {"name": "NV11", "aliases": ["NV11+"], "product": "nv9usb",
          "features": "An NV9USB+ with a note float recycler fitted on top."},
         {"name": "NV11 Spectral", "aliases": ["NV11S"], "product": "nv9_spectral",
          "features": "An NV9 Spectral with a note float recycler fitted on top."},
         {"name": "NV22 Spectral", "aliases": ["NV22", "NV22S"],
          "product": "nv9_spectral",
          "features": "An NV9 Spectral with a multi-note float fitted on top. "
                      "The multi-note float fits only the NV9 Spectral."}]},
    {"key": "icu", "name": "ICU", "aliases": [],
     "members": [
         {"name": "ICU Lite", "aliases": [], "product": "icu", "features": ""},
         {"name": "ICU Pro", "aliases": [], "product": "icu", "features": ""}]},
], "dismissed": [], "seen_products": None}


# ── storage ─────────────────────────────────────────────────────────────

def load() -> dict:
    data = jsonstore.load(_PATH, None, label="product families")
    if not (isinstance(data, dict) and isinstance(data.get("families"), list)):
        data = json.loads(json.dumps(_SEED))
    data.setdefault("dismissed", [])
    return data


def save(data: dict) -> dict:
    jsonstore.save(_PATH, data, label="product families")
    return data


def families() -> list[dict]:
    return load()["families"]


# ── matching ────────────────────────────────────────────────────────────

def _flat(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _forms(fam: dict) -> list[tuple[str, int | None]]:
    """(form, member index) for every name; None for the family's own."""
    out = [(_flat(f), None) for f in [fam.get("name"), *(fam.get("aliases") or [])]]
    for i, m in enumerate(fam.get("members") or []):
        out += [(_flat(f), i) for f in [m.get("name"), *(m.get("aliases") or [])]]
    return [(f, i) for f, i in out if f]


def _inside(small: str, big: str) -> bool:
    return small != big and f" {small} " in f" {big} "


def _generic(fam: dict) -> set[str]:
    """Forms that do not settle which member: the family's own, and a
    member's name inside a sibling's ("NV11" in "NV11 Spectral")."""
    forms = _forms(fam)
    gen = {f for f, i in forms if i is None}
    for f, i in forms:
        if any(j is not None and j != i and (g == f or _inside(f, g)) for g, j in forms):
            gen.add(f)
    return gen


def mentions(text: str, fam: dict) -> list[tuple[str, int | None, bool]]:
    """(form, member index, generic) for each name the text uses, a form
    inside a longer one it also uses dropped ("nv9" in "nv9 spectral")."""
    t = f" {_flat(text)} "
    hits = [(f, i) for f, i in _forms(fam) if f" {f} " in t]
    hits = [(f, i) for f, i in hits if not any(_inside(f, g) for g, _ in hits)]
    gen = _generic(fam)
    return [(f, i, f in gen) for f, i in hits]


def _picked(text: str, fam: dict, after_question: bool) -> list[int]:
    """Members the text settles on. Straight after we asked, a generic
    member name ("MyCheckr") is the pick; a general question is every one."""
    if f" {_flat(GENERAL)} {_flat(fam['name'])} " in f" {_flat(text)} ":
        return list(range(len(fam.get("members") or [])))
    ms = mentions(text, fam)
    got = sorted({i for _f, i, g in ms if i is not None and not g})
    if not got and after_question:
        got = sorted({i for _f, i, _g in ms if i is not None})
    return got


def asked_last_turn(history: list | None) -> bool:
    return bool(history) and MARKER in ((history[-1] or {}).get("a") or "").lower()


def _chosen_before(fam: dict, history: list | None) -> list[int]:
    """Members an earlier turn settled on, the latest choice."""
    last: list[int] = []
    after = False
    for h in history or []:
        got = _picked((h or {}).get("q") or "", fam, after)
        last = got or last
        after = MARKER in ((h or {}).get("a") or "").lower()
    return last


def _offered(fam: dict, product: str | None) -> list[int]:
    """The members to offer: those filed under the chat's product when it
    is one of them (an NV9USB+ chat: NV9USB+ or NV11), else all."""
    ms = fam.get("members") or []
    own = [i for i, m in enumerate(ms) if product and m.get("product") == product]
    return own or list(range(len(ms)))


def resolve(text: str, history: list | None = None, product: str | None = None,
            named: list[str] | tuple = ()) -> dict:
    """What the families make of one message.

    ask: the family to ask about, with `offer` (member indexes), when the
    message names it only generically, the chat's product is not exactly
    one of its members, no earlier turn chose, and the message is not a
    comparison (`named`: catalogue products it names, two or more).
    products: the catalogue products the members it settles on stand for.
    """
    after = asked_last_turn(history)
    ask, prods = None, []
    for fam in families():
        ms = fam.get("members") or []
        if not ms or not mentions(text, fam):
            continue
        got = _picked(text, fam, after)
        if not got and not after:
            got = _chosen_before(fam, history)
            member_products = [m.get("product") for m in ms]
            # Two products named is a comparison ("SH3 vs the SMART Coin
            # System"), answered for what it names.
            settled = (product and member_products.count(product) == 1) or \
                len(set(named)) >= 2
            if not got and ask is None and not settled:
                ask = {"family": fam, "offer": _offered(fam, product)}
        prods += [ms[i]["product"] for i in got if ms[i].get("product")]
    return {"ask": ask, "products": list(dict.fromkeys(prods))}


def without_generic(text: str) -> str:
    """The text with family names that settle nothing taken out, for the
    catalogue's own name matching: "SMART Hopper" is no longer read as
    the SMART Coin System's alias, which overrode a Smart Hopper 3 chat."""
    t = f" {_flat(text)} "
    for fam in families():
        # Models that all answer from one product leave its name standing:
        # "SMART Coin System" is still the SMART Coin System.
        if len({m.get("product") for m in fam.get("members") or []}) < 2:
            continue
        # A member's own name stays: the auto family "MyCheckr" is also the
        # MyCheckr's name, and taking it out left "the MyCheckr and the ICU
        # Pro" naming one product, so the comparison lost its MyCheckr arm.
        own = {f for f, i in _forms(fam) if i is not None}
        for f, _i, g in mentions(text, fam):
            if g and f not in own:
                t = t.replace(f" {f} ", " ")
    return t.strip()


_CONFUSED = re.compile(
    r"\b(?:not sure|unsure|no idea|don'?t know|do not know|idk|can'?t tell)\b.{0,40}"
    r"\b(?:which|what)\b|"
    r"\bhow (?:do|can|would) i (?:tell|know|identify|find out|check)\b.{0,30}\b(?:which|what)\b|"
    r"\bwhich (?:one|model|version|type) (?:do i have|have i got|is mine|i have)\b|"
    r"\bwhat (?:model|version|type) (?:do i have|is mine|have i got)\b", re.I)


def confused(text: str) -> bool:
    return bool(_CONFUSED.search(text or ""))


def family_for(text: str, history: list | None, product: str | None) -> dict | None:
    """The family a "not sure which I have" message is about: the one it
    names, else the one we just asked about, else the chat product's."""
    fams = families()
    for fam in fams:
        if mentions(text, fam):
            return fam
    if asked_last_turn(history):
        last = _flat(history[-1].get("a"))
        for fam in fams:
            if f" which {_flat(fam['name'])} " in f" {last} ":
                return fam
    for fam in fams:
        if any(m.get("product") == product for m in fam.get("members") or []):
            return fam
    return None


# ── telling them apart, one question at a time ──────────────────────────
# "I'm not sure which I have" asks the family's yes/no questions one by one
# ("Does a coin feeder sit on top?"), each the one that best halves the
# models still possible, until one is left. Where the family has no
# questions, or none tells the rest apart, every difference is listed.
# A question's `yes` names the models that have the trait.
_guides: dict = {}                 # session -> where its questions stand
_GUIDES_MAX = 2000
_YES = re.compile(r"\s*(?:yes|yeah|yep|y|it does|it is|there is)\b", re.I)
_NO = re.compile(r"\s*(?:no|nope|n|it doesn'?t|it isn'?t|there isn'?t)\b", re.I)
_UNSURE = re.compile(r"\s*(?:not sure|i'?m not sure|don'?t know|idk|no idea|can'?t tell)\s*[.!]?\s*$", re.I)


def _best_question(fam: dict, left: list[int], asked: list[int]) -> int | None:
    ms = fam.get("members") or []
    best = None
    for qi, qq in enumerate(fam.get("questions") or []):
        if qi in asked:
            continue
        yes = [i for i in left if ms[i]["name"] in (qq.get("yes") or [])]
        if 0 < len(yes) < len(left):
            score = abs(len(left) - 2 * len(yes))
            if best is None or score < best[0]:
                best = (score, qi)
    return None if best is None else best[1]


_YES_NO = [{"label": t, "features": "", "note": "", "picture": None, "product": "",
            "send": t} for t in ("Yes", "No", "Not sure")]


def _guide_next(session: str, st: dict) -> dict:
    fam, left = st["family"], st["left"]
    if len(left) == 1:
        _guides.pop(session, None)
        m = fam["members"][left[0]]
        return {"answer": f"That sounds like the {m['name']}. If that's yours, {MARKER}.",
                "options": options(fam, left, st.get("question"), general=False)[:-1] + [
                    {"label": "No, show me all of them", "features": "", "note": "",
                     "picture": None, "product": "", "send": f"Show me every {fam['name']}"}]}
    qi = _best_question(fam, left, st["asked"])
    if qi is None:
        _guides.pop(session, None)
        return {"answer": guide_reply(fam, left),
                "options": options(fam, left, st.get("question"), general=False)[:-1]}
    st["at"] = qi
    return {"answer": fam["questions"][qi]["q"], "options": _YES_NO}


def guide_start(session: str, fam: dict, offer: list[int], question: str | None) -> dict:
    """The first step for "not sure which I have"."""
    if len(_guides) >= _GUIDES_MAX:      # ponytail: drop the oldest; a store if it matters
        _guides.pop(next(iter(_guides)))
    st = {"family": fam, "left": list(offer), "asked": [], "at": None, "question": question}
    _guides[session] = st
    return _guide_next(session, st)


def guide_step(session: str, text: str) -> dict | None:
    """The next step when this message answers our yes/no question; None
    when no question is waiting or the message is not an answer (it ends
    the questions: the visitor moved on)."""
    st = _guides.get(session)
    if not st or st.get("at") is None:
        return None
    if _UNSURE.match(text or ""):
        verdict = None
    elif _YES.match(text or ""):
        verdict = True
    elif _NO.match(text or ""):
        verdict = False
    else:
        _guides.pop(session, None)
        return None
    qq = st["family"]["questions"][st["at"]]
    st["asked"].append(st["at"])
    if verdict is not None:
        ms = st["family"]["members"]
        st["left"] = [i for i in st["left"] if (ms[i]["name"] in (qq.get("yes") or [])) == verdict] \
            or st["left"]
    return _guide_next(session, st)


def show_all(text: str) -> dict | None:
    """The family a "Show me every SMART Hopper" message asks to list."""
    m = re.fullmatch(r"\s*show me every (.+?)\s*", text or "", re.I)
    if not m:
        return None
    return next((f for f in families() if _flat(f["name"]) == _flat(m.group(1))), None)


# ── replies ─────────────────────────────────────────────────────────────

def picture_url(member: dict) -> str | None:
    pic = member.get("picture") or {}
    if not (pic.get("source") and pic.get("name")):
        return None
    import visible
    if not visible.may_show(pic["source"]):
        return None
    return f"/figure/{quote(pic['source'])}/{quote(pic['name'])}"


def options(fam: dict, offer: list[int], question: str | None,
            general: bool = True) -> list[dict]:
    """A card per member: what tells it apart, its picture, and the message
    a click sends -- the original question naming the member, so it is
    answered for that one and the choice stays in the conversation."""
    ms = fam.get("members") or []
    out = []
    for i in offer:
        m = ms[i]
        send = f"{question} ({m['name']})" if question else f"I have the {m['name']}"
        out.append({"label": m["name"], "features": m.get("features") or "",
                    "note": _note(m), "picture": picture_url(m),
                    "product": m.get("product") or "", "send": send})
    if question and general and len(offer) > 1:
        names = ", ".join(ms[i]["name"] for i in offer)
        out.append({"label": "It's a general question", "features": "",
                    "note": "", "picture": None, "product": "",
                    "send": f"{question} ({GENERAL} {fam['name']}: {names})"})
    out.append({"label": "I'm not sure which I have", "features": "", "note": "",
                "picture": None, "product": "",
                "send": f"I'm not sure which {fam['name']} I have"})
    return out


def _note(member: dict) -> str:
    """The card's note: what the console typed, else "Obsolete since June
    2021" when the member's product is ticked obsolete (catalog)."""
    if member.get("note"):
        return member["note"]
    try:
        import catalog
        o = catalog.obsolete_of(member.get("product"))
    except Exception:
        return ""
    if not o:
        return ""
    text = (("Some units obsolete" if o.get("versions") else "Obsolete")
            + f" since {catalog.month_name(o['date'])}")
    if o.get("replacement"):
        text += f"; replaced by the {o['replacement']}"
    return text + "."


def question_reply(fam: dict) -> str:
    return (f"Which {fam['name']} do you have? The answer can differ between "
            f"them, so {MARKER}. Not sure? The differences are under each "
            "picture.")


def guide_reply(fam: dict, offer: list[int]) -> str:
    ms = fam.get("members") or []
    lines = []
    for i in offer:
        m = ms[i]
        bits = " ".join(x for x in (m.get("features"), _note(m)) if x)
        lines.append(f"- **{m['name']}**: {bits or 'see the picture'}")
    return (f"Here is how to tell which {fam['name']} you have:\n"
            + "\n".join(lines)
            + f"\n\nWhich one matches yours? {MARKER[0].upper() + MARKER[1:]}.")


def picked_reply(text: str, history: list | None) -> str | None:
    """"Got it" for a bare "I have the X" sent from the guide."""
    m = re.fullmatch(r"\s*i have the (.+?)\s*", text or "", re.I)
    if not (m and asked_last_turn(history)):
        return None
    for fam in families():
        for mem in fam.get("members") or []:
            if _flat(mem["name"]) == _flat(m.group(1)):
                note = f" {_note(mem)}" if _note(mem) else ""
                return (f"Thanks, that's the {mem['name']}.{note} What would "
                        "you like to know about it?")
    return None


# ── filling in automatically (after an upload, or from the console) ─────

_CODE = re.compile(r"^([a-z]+\d+)")
_PHOTO = re.compile(r"photo|render|isometric|perspective|three-quarter|"
                    r"product (?:overview|image)", re.I)
_NOT_PHOTO = re.compile(r"dimension|drawing|diagram|schematic|table|wiring|cable|"
                        r"connector|pin ?out|screenshot|flow ?chart|graph|label|"
                        r"logo|wordmark|certificate|qr|barcode|banner|cover|icon", re.I)


def _auto_groups(products: dict[str, str]) -> list[tuple[str, list[str]]]:
    """(family name, product keys) for products named alike: one model code
    ("NV9USB+", "NV9 Spectral" -> NV9) or one name inside another
    ("MyCheckr" in "MyCheckr mini")."""
    by_code: dict[str, list[str]] = {}
    for k, n in products.items():
        m = _CODE.match(_flat(n))
        if m:
            by_code.setdefault(m.group(1), []).append(k)
    groups = [(code.upper(), ks) for code, ks in by_code.items() if len(ks) > 1]
    flat = {k: _flat(n) for k, n in products.items()}
    for k, f in flat.items():
        inner = [o for o, g in flat.items() if o != k and _inside(f, g)]
        if inner and not any(k in ks for _n, ks in groups):
            groups.append((products[k], [k] + inner))
    return groups


def _distinct_words(fam: dict, member: dict) -> set[str]:
    """The words of a member's name its siblings' names lack ("lite",
    "spectral", "payout"); bare digits left out, they match any number."""
    names = [set(_flat(m.get("name")).split()) for m in fam.get("members") or []]
    common = set.intersection(*names) if names else set()
    return {w for w in set(_flat(member.get("name")).split()) - common if not w.isdigit()}


def _picture_score(fam: dict, member: dict, source: str, f: dict) -> float:
    """How much a figure looks like a photo of this member. The picture
    description says what kind of figure it is in its first words ("Photo
    of", "Wiring/assembly drawing"); a crop without one is judged by its
    printed text, where a dimension drawing is mostly numbers."""
    head = " ".join([str(f.get("title") or ""), str(f.get("desc") or "")[:90]])
    text = " ".join(str(f.get(k) or "") for k in ("title", "caption", "desc", "text"))
    flat = f" {_flat(text)} "
    forms = [_flat(x) for x in [member.get("name"), *(member.get("aliases") or [])] if _flat(x)]
    score = 0.0
    score += 3 * any(f" {x} " in flat for x in forms)
    score += 2 * any(f" {w} " in flat for w in _distinct_words(fam, member))
    score += 4 * bool(_PHOTO.search(head)) - 6 * bool(_NOT_PHOTO.search(head))
    if len(re.findall(r"\b\d+(?:\.\d+)?\b", str(f.get("text") or ""))) >= 8:
        score -= 3
    try:
        x0, y0, x1, y1 = [float(v) for v in (f.get("bbox") or [0, 0, 0, 0])[:4]]
        score -= 2 * (y1 - y0 > 560)           # a whole-page sheet
        page = int(f.get("page") or 99)
    except (TypeError, ValueError):
        page = 99
    score += 1 * (page <= 10) + 1 * ("manual" in source.lower())
    return score


def _pick_picture(fam: dict, member: dict, taken: set) -> dict | None:
    """The figure most like a photo of this member, from its product's
    documents; None when nothing scores above zero."""
    import docindex
    import figures
    import visible
    b = visible.blocked()
    # Below 3 (a name match and a page bonus) it is no photo: no card
    # picture is better than a certificate or a QR code.
    best, best_score = None, 2.9
    for row in docindex.source_index():
        if member.get("product") not in (row.get("products") or []):
            continue
        if not visible.may_show(row["source"], row.get("products") or [], b):
            continue
        for name, f in (figures.figures_for(row["source"]) or {}).items():
            if (row["source"], name) in taken:
                continue
            score = _picture_score(fam, member, row["source"], f)
            if score > best_score:
                best, best_score = {"source": row["source"], "name": name}, score
    return best


def _draft_features(fam: dict, idxs: list[int]) -> dict[int, str]:
    """"How to tell" text for the members in `idxs`, from each member's own
    passages, by the fast model. Facts from the passages only."""
    import llm
    import retrieval_db
    import visible
    b = visible.blocked()
    ms = fam["members"]
    blocks = []
    for m in ms:
        try:
            got = retrieval_db.retrieve_from_db(
                f"{m['name']} appearance features front panel what it looks like",
                top_k=12, scope={"product": m["product"]} if m.get("product") else None)
        except Exception as exc:
            logger.warning("family draft: retrieval for %r failed: %s", m["name"], exc)
            got = []
        got = [r for r in got
               if visible.may_show(r.get("source"), r.get("products") or r.get("product") or "", b)][:4]
        texts = [" ".join((r.get("text") or "").split())[:600] for r in got[:4]]
        blocks.append(f'Product "{m["name"]}":\n' + "\n".join(f"- {t}" for t in texts))
    want = [ms[i]["name"] for i in idxs]
    prompt = (
        f"Customers mix up these {fam['name']} products. Passages from each "
        "one's documentation:\n\n" + "\n\n".join(blocks) + "\n\n"
        "For each of: " + ", ".join(want) + "\n"
        "write one or two short sentences a customer standing at the device "
        "can check to tell it apart from the others (something they can see "
        "or do: a part, slot, label, screen, button, size). Use only facts "
        "in the passages; if they give none for a product, write an empty "
        "string. Reply with JSON only, product name -> text.")
    try:
        out = llm.generate_with_fallback("fast", prompt)
        raw = (out or {}).get("text") or ""
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception as exc:
        logger.warning("family draft for %r failed: %s", fam["name"], exc)
        return {}
    return {i: str(data.get(ms[i]["name"]) or "").strip()[:400]
            for i in idxs if data.get(ms[i]["name"])}


def _draft_questions(fam: dict) -> list[dict]:
    """Yes/no questions that tell the members apart, from the differences
    already written for them, by the fast model; [] when it gives none."""
    import llm
    ms = [m for m in fam["members"] if m.get("features")]
    if len(ms) < 2:
        return []
    names = [m["name"] for m in fam["members"]]
    prompt = (
        f"Customers mix up these {fam['name']} products:\n"
        + "\n".join(f"- {m['name']}: {m['features']}" for m in ms) + "\n\n"
        "Write up to 3 yes/no questions a customer standing at the device can "
        "answer, which together tell these products apart. Use only the "
        "differences above. Reply with JSON only: a list of objects "
        '{"q": question, "yes": [the product names for which the answer is yes]}.')
    try:
        out = llm.generate_with_fallback("fast", prompt)
        raw = (out or {}).get("text") or ""
        data = json.loads(raw[raw.find("["): raw.rfind("]") + 1])
    except Exception as exc:
        logger.warning("family questions for %r failed: %s", fam["name"], exc)
        return []
    return [{"q": str(d.get("q") or "").strip()[:200],
             "yes": [n for n in d.get("yes") or [] if n in names], "auto": True}
            for d in data if isinstance(d, dict) and d.get("q")][:3]


def refresh_auto(draft: bool = True) -> dict:
    """Join new products to families, add families for new look-alike
    products, pick missing pictures and draft missing differences. Fields
    an admin set are kept. Returns the saved data; a refresh already
    running returns the data as it stands."""
    if not _refreshing.acquire(blocking=False):
        return load()
    try:
        import catalog
        import visible
        data = load()
        products = {p["key"]: p["name"] for c in catalog.catalog()["categories"]
                    for p in c["products"] if not p["key"].endswith("_general")}
        seen = data.get("seen_products")
        new = set(products) - set(seen or products)
        data["seen_products"] = sorted(products)
        fams = data["families"]
        # D-20261106-07: features and questions drafted before the hidden-
        # document filter may hold hidden text; redraft them once.
        if draft and not data.get("drafted_visible_only"):
            for fam in fams:
                for m in fam.get("members") or []:
                    if m.get("features_auto"):
                        m["features"] = ""
                        m.pop("features_auto", None)
                qs = fam.get("questions") or []
                if qs and all(q.get("auto") for q in qs):
                    fam["questions"] = []
            data["drafted_visible_only"] = True
        in_family = {m.get("product") for f in fams for m in f.get("members") or []}
        # A new product whose name carries a family's name joins it.
        for k in sorted(new - in_family):
            for fam in fams:
                if any(f" {f} " in f" {_flat(products[k])} " for f, i in _forms(fam) if i is None):
                    fam["members"].append({"name": products[k], "aliases": [],
                                           "product": k, "features": "", "auto": True})
                    in_family.add(k)
                    break
        # Look-alike products in no family yet become one.
        for name, keys in _auto_groups(products):
            key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            if key in data["dismissed"] or any(f["key"] == key for f in fams):
                continue
            if any(k in in_family for k in keys):
                continue
            fams.append({"key": key, "name": name, "aliases": [], "auto": True,
                         "members": [{"name": products[k], "aliases": [], "product": k,
                                      "features": "", "auto": True} for k in keys]})
            in_family |= set(keys)
        for fam in fams:
            taken = {(m["picture"]["source"], m["picture"]["name"])
                     for m in fam["members"] if m.get("picture")}
            for m in fam["members"]:
                if m.get("picture") and not visible.may_show(m["picture"].get("source")):
                    m.pop("picture")
                if not m.get("picture"):
                    try:
                        pic = _pick_picture(fam, m, taken)
                    except Exception as exc:
                        logger.warning("family picture for %r failed: %s", m["name"], exc)
                        pic = None
                    if pic:
                        m["picture"] = dict(pic, auto=True)
                        taken.add((pic["source"], pic["name"]))
            missing = [i for i, m in enumerate(fam["members"]) if not m.get("features")]
            if draft and missing and len(fam["members"]) > 1:
                for i, text in _draft_features(fam, missing).items():
                    fam["members"][i]["features"] = text
                    fam["members"][i]["features_auto"] = True
            if draft and not fam.get("questions") and len(fam["members"]) > 1:
                fam["questions"] = _draft_questions(fam)
        return save(data)
    finally:
        _refreshing.release()


def refresh_in_background() -> None:
    threading.Thread(target=lambda: _safe(refresh_auto), daemon=True,
                     name="families-refresh").start()


def _safe(fn) -> None:
    try:
        fn()
    except Exception:
        logger.exception("product families refresh failed")


def pictures_for(product: str, limit: int = 60) -> list[dict]:
    """Every figure of a product's documents, for the console's picker."""
    import docindex
    import figures
    import visible
    b = visible.blocked()
    out = []
    for row in docindex.source_index():
        if product not in (row.get("products") or []):
            continue
        if not visible.may_show(row["source"], row.get("products") or [], b):
            continue
        for name, f in sorted((figures.figures_for(row["source"]) or {}).items(),
                              key=lambda kv: (kv[1].get("page") or 0, kv[0])):
            out.append({"source": row["source"], "name": name, "page": f.get("page"),
                        "caption": f.get("title") or f.get("caption") or "",
                        "url": picture_url({"picture": {"source": row["source"], "name": name}})})
            if len(out) >= limit:
                return out
    return out


def validate(fams: list) -> list[dict]:
    """The console's edit, checked: keys and names present, members named."""
    if not isinstance(fams, list):
        raise ValueError("families must be a list")
    keys = set()
    out = []
    for f in fams:
        key = re.sub(r"[^a-z0-9_]+", "_", str(f.get("key") or f.get("name") or "").lower()).strip("_")
        name = str(f.get("name") or "").strip()
        if not key or not name or key in keys:
            raise ValueError(f"each family needs a name, and its own: {name or key!r}")
        keys.add(key)
        members = []
        for m in f.get("members") or []:
            mname = str(m.get("name") or "").strip()
            if not mname:
                raise ValueError(f"a member of {name} has no name")
            pic = m.get("picture") if isinstance(m.get("picture"), dict) else None
            members.append({
                "name": mname[:80],
                "aliases": [str(a).strip()[:60] for a in m.get("aliases") or [] if str(a).strip()],
                "product": str(m.get("product") or "").strip(),
                "features": str(m.get("features") or "").strip()[:600],
                "note": str(m.get("note") or "").strip()[:300],
                **({"features_auto": True} if m.get("features_auto") else {}),
                **({"picture": {"source": str(pic.get("source")), "name": str(pic.get("name"))}}
                   if pic and pic.get("source") and pic.get("name") else {}),
            })
        names = {m["name"] for m in members}
        questions = [{"q": str(qq.get("q") or "").strip()[:200],
                      "yes": [n for n in qq.get("yes") or [] if n in names],
                      **({"auto": True} if qq.get("auto") else {})}
                     for qq in f.get("questions") or [] if str(qq.get("q") or "").strip()]
        out.append({"key": key, "name": name[:60],
                    "aliases": [str(a).strip()[:60] for a in f.get("aliases") or [] if str(a).strip()],
                    "members": members, "questions": questions,
                    **({"auto": True} if f.get("auto") else {})})
    return out
