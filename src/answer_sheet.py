"""Answer sheet: code-side checks on the figures an answer is built from.

The model fills a sheet (rows of product / item / value / passage id, plus
verdict requests); this module checks every row against the passage it cites,
lists what is missing or contradicted, and computes the verdicts (lower, higher,
within a rating, each product yes/no, plain value) in code, not in prose.

Pure: stdlib only, no model call, no import from main.py or any app module.
Rows carry catalogue keys; mapping product names to keys is the caller's job.
"""
import json
import math
import re

_NUM = re.compile(r"(?:(?<![A-Za-z0-9.,])|(?<=\d[xX]))([-+−]?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?")
_HEX = re.compile(r"(?<![A-Za-z0-9])0x[0-9a-fA-F]+")
_UNIT = re.compile(r"\s*([A-Za-zµ°%]+)")
_ALIAS = {"amps": "a", "amp": "a", "volts": "v", "volt": "v"}
_SCALE = {"ma": ("a", 1e-3), "mv": ("v", 1e-3), "mw": ("w", 1e-3), "kw": ("w", 1e3), "g": ("kg", 1e-3)}


def _key(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _matches(text):
    """Yield (number, text after it); hex tokens are blanked out first."""
    text = _HEX.sub(lambda m: " " * len(m.group()), str(text or ""))
    for m in _NUM.finditer(text):
        n = float(m.group(2).replace(",", "") + ("." + m.group(3) if m.group(3) else ""))
        yield (-n if m.group(1) in ("-", "−") else n), text[m.end():]


def _numbers(text):
    return [n for n, _ in _matches(text)]


def _in_passage(value, text):
    value, text = str(value), str(text or "")
    nums, hexes = _numbers(value), _HEX.findall(value)
    if nums or hexes:
        have = set(_numbers(text))
        return all(n in have for n in nums) and all(h.lower() in text.lower() for h in hexes)
    return " ".join(value.lower().split()) in " ".join(text.lower().split())


def _quantity(value):
    for n, rest in _matches(value):
        m = _UNIT.match(rest)
        unit = m.group(1).lower().replace("°", "") if m else ""
        unit = _ALIAS.get(unit, unit)
        if unit in _SCALE:
            unit, f = _SCALE[unit]
            n *= f
        return n, unit
    return None


def _same(a, b):
    qa, qb = _quantity(a), _quantity(b)
    if qa and qb:
        return qa[1] == qb[1] and math.isclose(qa[0], qb[0], rel_tol=1e-9, abs_tol=1e-12)
    return " ".join(str(a).lower().split()) == " ".join(str(b).lower().split())


def _not_found(v):
    return v is None or str(v).strip().lower() in ("", "not found")


def parse_sheet(raw):
    raw = (raw or "").strip()
    m = re.match(r"```(?:json)?\s*(.*?)\s*```$", raw, re.S)
    try:
        d = json.loads(m.group(1) if m else raw)
    except ValueError:
        return None
    return d if isinstance(d, dict) and isinstance(d.get("rows"), list) else None


def _items(v):
    it = v.get("items") or []
    return [it] if isinstance(it, str) else list(it)


def _pick(rows, prod, item, cond=None):
    for r in rows:
        if _key(r["product"]) == _key(prod) and _key(r["item"]) == _key(item) \
                and (not cond or _key(cond) in _key(r.get("condition"))):
            return r


def _compare(v, rows, products):
    """-> (verdict, per_product, note) for one verdict request."""
    kind, cond = v.get("kind"), v.get("condition")

    def get(item, p):
        return _pick(rows, p, item, cond)

    def nf(p):
        return None, {}, f"not found for {p}"

    if kind in ("lower", "higher"):
        qs = {}
        for p in products:
            r = get(v.get("item"), p)
            q = r and _quantity(r["value"])
            if not q:
                return nf(p)
            qs[p] = q
        if len({u for _, u in qs.values() if u}) > 1:
            return None, {}, "units differ"
        nums = {p: q[0] for p, q in qs.items()}
        best = (min if kind == "lower" else max)(nums.values())
        win = [p for p, n in nums.items() if n == best]
        return (win[0] if len(win) == 1 else "same"), nums, ""
    if kind == "within":
        p = v.get("product") or (products[0] if products else "")
        qs = {}
        for it in _items(v) + [v.get("limit")]:
            r = get(it, p)
            q = r and _quantity(r["value"])
            if not q:
                return nf(p)
            qs[it] = q
        lim = qs.pop(v.get("limit"))
        if len({u for _, u in list(qs.values()) + [lim] if u}) > 1:
            return None, {}, "units differ"
        nums = {k: q[0] for k, q in qs.items()}
        if v.get("combine", "sum") == "sum":
            nums = {"total": sum(nums.values())}
        up = v.get("direction", "at_most") == "at_most"
        ok = all(n <= lim[0] if up else n >= lim[0] for n in nums.values())
        nums["limit"] = lim[0]
        return ("Yes" if ok else "No"), {p: nums}, ""
    if kind == "each":
        per = {}
        for p in products:
            r = get(v.get("item"), p)
            ans = _key(r.get("answer")) if r else ""
            per[p] = ans if ans in ("yes", "no") else None
            if per[p] is None:
                return nf(p)
        vals = set(per.values())
        return ("Yes" if vals == {"yes"} else "No" if vals == {"no"} else "Mixed"), per, ""
    if kind == "value":
        per = {}
        for p in products:
            r = get(v.get("item"), p)
            if not r:
                return nf(p)
            per[p] = r["value"]
        return (next(iter(per.values())) if len(per) == 1 else None), per, ""
    return None, {}, "unknown kind"


def check(sheet, passages, products=None, shared=()):
    passages = {str(k): v for k, v in passages.items()}
    # model output: skip rows that are not dicts naming a product and an item
    rows_in = [r for r in sheet.get("rows") or [] if isinstance(r, dict) and r.get("product") and r.get("item")]
    products = products or sheet.get("products") or list(dict.fromkeys(r["product"] for r in rows_in))
    shared = {_key(s) for s in shared}
    kept, dropped = [], []
    for r in rows_in:
        if _not_found(r.get("value")):
            continue
        p = passages.get(str(r.get("passage")))
        reason = None
        if p is None:
            reason = "no such passage"
        else:
            text = p.get("text", "")
            tags = {_key(t) for t in str(p.get("product", "")).split(",") if _key(t)}
            if tags and not (tags & shared) and _key(r["product"]) not in tags:
                reason = "passage is about " + str(p.get("product"))
            elif not _in_passage(r["value"], text):
                reason = "value not in passage"
            elif r.get("device") and _key(r["device"]) not in _key(p.get("section") or text):
                reason = "passage is about another device"
        if reason:
            dropped.append(dict(r, reason=reason, note=f"not found for {r['product']}"))
        else:
            kept.append(r)
    missing = [{"product": p, "item": a, "note": f"not found for {p}"}
               for a in sheet.get("asks", []) for p in products if not _pick(kept, p, a)]
    groups = {}
    for r in kept:
        groups.setdefault((_key(r["product"]), _key(r["item"]), _key(r.get("condition"))), []).append(r)
    conflicts, bad = [], set()
    for k, g in groups.items():
        vals = []
        for r in g:
            if not any(_same(r["value"], x) for x in vals):
                vals.append(r["value"])
        if len(vals) > 1:
            bad.add(k)
            conflicts.append({"product": g[0]["product"], "item": g[0]["item"],
                              "condition": g[0].get("condition"), "values": vals})
    verdicts = []
    for v in [v for v in sheet.get("verdicts") or [] if isinstance(v, dict)]:
        items = {_key(i) for i in [v.get("item"), v.get("limit")] + _items(v) if i}
        mine = [r for r in kept if _key(r["item"]) in items
                and (not v.get("condition") or _key(v["condition"]) in _key(r.get("condition")))]
        if any((_key(r["product"]), _key(r["item"]), _key(r.get("condition"))) in bad for r in mine):
            res = (None, {}, "documents disagree")
        else:
            res = _compare(v, mine, products)
        verdicts.append(dict(v, verdict=res[0], per_product=res[1], note=res[2]))
    return {"rows": kept, "dropped": dropped, "missing": missing,
            "conflicts": conflicts, "verdicts": verdicts}
