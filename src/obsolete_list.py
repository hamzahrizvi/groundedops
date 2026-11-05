"""The support hub's "Obsolete Product Notification" page, read as ticks to
offer on the console's Products page (CEO 2026-11-05).

The page is one table per product: name, label identifier, dataset code,
manufactured date and obsolescence date (the SMART Hopper's has a software
date and a service-and-repairs date). A row whose label and dataset are
"All" covers every unit, so its answers are blocked by default; otherwise
only some units are obsolete ("NV200, BR29 & below") and blocking every
answer about the product would be wrong, so the suggestion leaves it off.
Nothing is applied here: the console shows each one with an Apply button.
"""
from __future__ import annotations

import re

_MONTHS = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def _flat(text: str) -> str:
    """"NV9USB+" stays apart from "NV9USB": the plus is a different product."""
    return re.sub(r"[^a-z0-9+]+", "", (text or "").lower())


def month(text: str) -> str:
    """"June 2021", "Sept 2019" -> "2021-06", "2019-09"; "" otherwise."""
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{4})", text or "")
    if not m or m.group(1).lower() not in _MONTHS:
        return ""
    return f"{m.group(2)}-{_MONTHS[m.group(1).lower()]:02d}"


def rows(text: str) -> list[dict]:
    """Every table row of the page: name, versions, date, service_end."""
    from chunking import TABLE_CLOSE, TABLE_OPEN
    out = []
    for block in re.findall(re.escape(TABLE_OPEN) + r"(.*?)" + re.escape(TABLE_CLOSE),
                            text or "", re.S):
        lines = [[c.strip() for c in ln.split("|")] for ln in block.strip().splitlines()
                 if "|" in ln]
        if len(lines) < 2:
            continue
        head = [h.lower() for h in lines[0]]
        dates = [i for i, h in enumerate(head) if "obsolescence" in h]
        if not dates:
            continue
        for cells in lines[1:]:
            get = lambda i: cells[i] if i < len(cells) else ""  # noqa: E731
            label, dataset, made = get(1), get(2), get(3)
            whole = label.lower().startswith("all") and dataset.lower() == "all"
            versions = "" if whole else ", ".join(x for x in (
                "" if label.lower() == "all" else
                f"{label} label" if label and not label.lower().startswith("firmware") else label,
                f"dataset {dataset}" if dataset and dataset.lower() != "all" else "",
                f"made {made[0].lower() + made[1:]}" if made and made.lower() != "all" else "") if x)
            out.append({"name": get(0), "versions": versions, "whole": whole,
                        "date": month(get(dates[0])),
                        "service_end": month(get(dates[1])) if len(dates) > 1 else ""})
    return out


def _page_text() -> tuple[str, str] | None:
    import docindex
    import docstore
    for row in docindex.source_index():
        src = row["source"]
        if "obsolete product" in src.lower() and src.lower().endswith(".txt"):
            path = docstore.find(src)
            if path:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    return src, fh.read()
    return None


def suggestions(catalog: dict, page: tuple[str, str] | None = None) -> list[dict]:
    """One per catalogue product the page names, rows for it combined: the
    latest date, every version listed. By the product's own name only: an
    alias is often the older model the page means ("NV200" is the NV200S's
    alias, and the page's NV200 is the old validator)."""
    page = page if page is not None else _page_text()
    if not page:
        return []
    source, text = page
    forms = {}
    for c in catalog.get("categories") or []:
        for p in c.get("products") or []:
            if _flat(p.get("name")):
                forms.setdefault(_flat(p.get("name")), p)
    out: dict[str, dict] = {}
    for r in rows(text):
        p = forms.get(_flat(r["name"]))
        if not p or not r["date"]:
            continue
        s = out.setdefault(p["key"], {"product_key": p["key"], "product_name": p["name"],
                                      "source": source, "rows": [], "obsolete": {
                                          "date": "", "service_end": "", "versions": "",
                                          "replacement": "", "block_answers": True,
                                          "confluence_import": True}})
        s["rows"].append(r)
        o = s["obsolete"]
        o["date"] = max(o["date"], r["date"])
        o["service_end"] = max(o["service_end"], r["service_end"])
        if not r["whole"]:
            o["block_answers"] = False
            own = r["versions"] if _flat(r["name"]) == _flat(p["name"]) else f"{r['name']}: {r['versions']}"
            o["versions"] = "; ".join(x for x in (o["versions"], own) if x)
    # Only what is still to decide: a product not ticked, or ticked with
    # other dates than the list now gives ("changed"); not one dismissed
    # at the date the list gives.
    keep = []
    for s in out.values():
        p = next(p for c in catalog.get("categories") or [] for p in c.get("products") or []
                 if p["key"] == s["product_key"])
        have = p.get("obsolete") or {}
        want = s["obsolete"]
        if have and have.get("date") == want["date"] and                 (have.get("service_end") or "") == want["service_end"]:
            continue
        if p.get("obsolete_dismissed") == want["date"]:
            continue
        s["changed"] = bool(have)
        keep.append(s)
    return sorted(keep, key=lambda s: s["product_name"].lower())
