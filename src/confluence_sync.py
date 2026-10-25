"""Manuals from one Confluence folder or page (the "Website" one), with a
review step between Confluence and the bot.

Two steps, kept apart so a person can stand between them:

  check  -- list what is under the root: every page with real text and every
            PDF, Word or text file attached to those pages. Records each as
            an item with its Confluence version. Indexes nothing.
  apply  -- bring the index in line with the approved items: add new ones,
            re-index changed ones, remove ones excluded or gone from
            Confluence. Only documents this sync added are ever removed.

"Sync now" and the daily run do both. Each item is approved or excluded;
anything not approved is excluded. "Internal" syncs a document but keeps it
from customers' answers. In "auto" mode a newly found document is
approved at once (sync everything); in "review" mode it starts excluded and
marked new until someone approves it on the console's Confluence page. A
document that was approved stays approved when it changes in Confluence; in
review mode its new version waits for approval like a new document (the old
one keeps answering), in auto mode it syncs.

A folder can be combined into one document: a manual Confluence keeps as
a page per command (the SSP Manual: 396 of them) is indexed as one source,
each page a section under its own heading, rather than 396 sources nobody
can file. The innermost combined folder wins, so "SDK" can be combined while
each device's endpoint pages inside it are their own document.

A manual already uploaded by hand (most were PDFs exported from these
same pages) is matched to its Confluence copy by title, version and date
left out. When the Confluence copy is the whole manual -- a combined
folder, a page with no chapters under it, or a file -- syncing it replaces
the upload: the upload's product filing carries over, and the upload is
removed once the Confluence copy is in. A manual Confluence keeps as a
page per chapter has to be combined first, or the cover page would replace
the whole PDF. "Keep both" opts a document out.

Each item also records when Confluence last saw it edited, when this sync
first found it and when its version last changed between two checks, so the
console can show what is new and what has been updated.

Filing: a document already filed in the catalogue keeps that filing. A new
one is suggested the product its nearest folder or page is named after,
and the suggestion can be changed before or after it is synced.

Access is a scoped Atlassian API token (read-only granular scopes) for an
account that can see only what customers may see. Scoped tokens work only
through api.atlassian.com, addressed by the site's cloud id.
"""
import hashlib
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import requests

import catalog
import jsonstore
import keystore

logger = logging.getLogger(__name__)

_STATE = os.getenv("CONFLUENCE_SYNC_STATE", "confluence_sync.json")
_FILE_TYPES = (".pdf", ".docx", ".txt")
# A page with less text than this is a wrapper round its attachment or a
# list of child pages, not a manual, and is not offered.
_MIN_PAGE_TEXT = 200
_HISTORY = 20
MODES = ("review", "auto")
# "internal" syncs like "approved" but the document is marked internal
# (retrieval_db.is_excluded): kept in the index, never used to answer a
# customer, ready for a staff mode.
DECISIONS = ("approved", "internal", "excluded")
# A title that calls itself internal ("X (Internal)", "INTERNAL - Y",
# "spec_internal.pdf") is filed internal, not approved, when auto mode
# finds it; not "internals" or "international". An admin can still
# approve it on the Confluence page; only a first sighting is filed this way.
_INTERNAL_TITLE = re.compile(r"(?<![a-z])internal(?![a-z])", re.I)
_RECENT_DAYS = 14                  # how long "new" and "updated" flags last
# Attachments larger than this are not downloaded; the run names them in its
# errors. Uploads have no cap of their own (main.py exempts /upload).
MAX_FILE_BYTES = int(float(os.getenv("CONFLUENCE_MAX_FILE_MB", "100")) * 1024 * 1024)

# A folder, or a page whose child pages hold the manuals: both are a
# "Website" container in Confluence, depending on how the space was built.
_FOLDER_URL = re.compile(
    r"^https://([a-z0-9-]+\.atlassian\.net)/wiki/spaces/[^/]+/(folder|pages)/(\d+)", re.I)

_run_lock = threading.Lock()      # one check/sync at a time
_state_lock = threading.Lock()    # read-modify-write of the state file
_progress: dict = {}
# Set by cancel(); checked between pages (check) and between documents (sync).
_cancel = threading.Event()


class Cancelled(RuntimeError):
    pass


class _Moved(Exception):
    """Edited in Confluence after a person approved it."""


def cancel() -> bool:
    """Ask the run in progress to stop at its next page or document. A check
    stopped part way is thrown away (it writes only at the end); a sync keeps
    the documents it finished, and the rest wait for the next one."""
    if not _run_lock.locked():
        return False
    _cancel.set()
    _progress["cancelling"] = True
    return True


def _stop_point() -> None:
    if _cancel.is_set():
        raise Cancelled("Stopped by request")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── settings ────────────────────────────────────────────────────────────

def _match_folder_url(url: str) -> re.Match:
    m = _FOLDER_URL.match((url or "").strip())
    if not m:
        raise ValueError("Paste the link of the Confluence folder or page that holds "
                         "the manuals, like https://yoursite.atlassian.net/wiki/spaces/KEY/"
                         "folder/123456 or .../KEY/pages/123456/Website")
    return m


def parse_folder_url(url: str) -> tuple[str, str, str]:
    """(site, "folders" or "pages", id) from the link. The site is checked
    against *.atlassian.net because the sync fetches from it."""
    m = _match_folder_url(url)
    return m.group(1).lower(), "folders" if m.group(2).lower() == "folder" else "pages", m.group(3)


def normalise_folder_url(url: str) -> str:
    """The link without whatever the browser added after the id (a page's
    title slug, tracking parameters)."""
    return _match_folder_url(url).group(0)


def configured() -> bool:
    s = keystore.get_confluence_settings()
    return bool(s["folder_url"] and s["email"] and s["token_set"])


def running(since: int = 0) -> dict | None:
    """What a run in progress is doing, or None. `log` is what it has found
    or indexed so far, from entry `since` on, so a console polling every
    second or two is sent only what is new."""
    if not _run_lock.locked():
        return None
    log = list(_progress.get("log", []))
    return {**{k: v for k, v in _progress.items() if k != "log"},
            "log": log[since:], "log_total": len(log)}


def _note(title: str, kind: str, path: list[str], action: str) -> None:
    _progress.setdefault("log", []).append(
        {"title": title, "kind": kind, "path": " › ".join(path), "action": action})


# ── state ───────────────────────────────────────────────────────────────

def _load() -> dict:
    st = jsonstore.load(_STATE, {}, label="Confluence sync")
    if "sources" in st and "items" not in st:
        # The first version kept only {source: "page:<id>:<version>"}.
        items = {}
        for src, tag in st.pop("sources").items():
            kind, cid, ver = (tag.split(":") + ["", ""])[:3]
            items[f"{kind}:{cid}"] = {
                "kind": "page" if kind == "page" else "file", "id": cid,
                "title": os.path.splitext(src)[0], "source": src, "path": [],
                "version": int(ver) if ver.isdigit() else None, "present": True,
                "decision": "approved",
                "synced_version": int(ver) if ver.isdigit() else None}
        st["items"] = items
    st.setdefault("mode", "review")
    st.setdefault("items", {})
    st.setdefault("history", [])
    if "holds_since" not in st:     # updates found before holds existed must not go out unseen
        st["holds_since"] = _now()
        if st["mode"] == "review":
            for it in st["items"].values():
                if it.get("decision") != "excluded" and it.get("present", True) \
                        and it.get("synced_version") is not None \
                        and it.get("version") != it["synced_version"]:
                    it["held"] = True
    # A combined folder is {"id": its Confluence id, "path": its titles as last
    # seen}. Entries written before ids were plain title lists; a check pins
    # each to its id.
    st["bundles"] = [b if isinstance(b, dict) else {"path": b} for b in st.get("bundles", [])]
    for it in st["items"].values():  # "pending" was a third decision; not approved is excluded
        if it.get("decision") not in DECISIONS:
            it["decision"] = "excluded"
    for h in st["history"]:          # entries written before history kept counts
        for k, v in list(h.items()):
            if isinstance(v, list) and k != "errors":
                h[k] = len(v)
    return st


def _save(st: dict) -> None:
    jsonstore.save(_STATE, st, label="Confluence sync")


def _update(fn) -> dict:
    """Load, change with fn(state), save, atomically against other writers."""
    with _state_lock:
        st = _load()
        fn(st)
        _save(st)
        return st


def status_of(it: dict) -> str:
    """One word for what the next sync will do with an item, or has done."""
    synced = it.get("synced_version") is not None
    if it.get("error"):
        return "error"
    if not it.get("present", True):
        if it.get("combined"):
            return "combined" if synced else "missing"
        return "gone" if synced else "missing"
    if it.get("decision") == "excluded":
        return "remove" if synced else "excluded"
    if it.get("held"):
        return "held"           # edited in Confluence; waits for approval (review mode)
    if not synced:
        return "new"
    return "synced" if it.get("synced_version") == it.get("version") else "changed"


def _filed(source: str) -> tuple[str | None, str]:
    """(category key, "k1,k2") a source is filed under in the catalogue."""
    data = catalog.load_catalog()
    held = [(c["key"], p["key"]) for c in data["categories"]
            for p in c.get("products", []) if source in p.get("sources", [])]
    return (held[0][0] if held else None), ",".join(k for _, k in held)


def _filed_all() -> dict[str, str]:
    """source -> "k1,k2" for every filed source, in one catalogue read."""
    filed: dict[str, str] = {}
    for c in catalog.load_catalog()["categories"]:
        for p in c.get("products", []):
            for src in p.get("sources", []):
                filed[src] = (filed[src] + "," if src in filed else "") + p["key"]
    return filed


def _norm(name: str) -> str:
    """A product or folder name reduced to what identifies it: Confluence
    calls the folder "NV9 Spectral Range", "SMART Coin System Range
    Documentation" or "NV4000 Endpoints" where the catalogue says "NV9
    Spectral"."""
    n = re.sub(r"\(.*?\)", " ", (name or "").lower())
    n = re.sub(r"\b(range|documentation|docs|endpoints)\b", " ", n)
    return re.sub(r"[^a-z0-9+]+", "", n)


def _name_index() -> dict[str, list]:
    """norm(name or alias) -> [(category key, product key)]."""
    idx: dict[str, list] = {}
    for c in catalog.load_catalog()["categories"]:
        for p in c.get("products", []):
            for n in [p["name"], *p.get("aliases", [])]:
                if _norm(n):
                    idx.setdefault(_norm(n), []).append((c["key"], p["key"]))
    return idx


def _suggest(path: list[str], idx: dict | None = None) -> tuple[str | None, str]:
    """The product the nearest folder or page above is named after. A folder
    "C-SCS - Compact SMART Coin System" is tried whole and by each half."""
    idx = _name_index() if idx is None else idx
    for title in reversed(path or []):
        for part in [title, *title.split(" - ")]:
            n = _norm(part)
            # The longest product name the folder's name starts with, not only
            # an exact one: "MyCheckr Mini User Manual" is the Mini's, and
            # matching exactly skipped it for "MyCheckr Range Documentation"
            # above, filing every Mini declaration under the MyCheckr.
            best = n if n in idx else max((k for k in idx if len(k) >= 3 and n.startswith(k)),
                                          key=len, default="")
            hit = idx.get(best)
            if hit and len(set(hit)) == 1:  # a name two categories share files nothing
                return hit[0]
    return None, ""


def _effective(it: dict, idx: dict) -> tuple[str | None, str, bool]:
    """(category, products, suggested) an unsynced item would be filed
    under: what someone chose, else what its folders suggest now (so a
    product added to the catalogue later is picked up). The product a
    check stored when it first found the item is a suggestion too: only a
    person's choice is filing."""
    if it.get("product_chosen"):
        return it.get("category") or category_of(it.get("product")), it.get("product") or "", False
    # A combined document is named after its folder, which is where the
    # product usually is ("NV4000 Endpoints").
    own = [it["title"]] if it.get("kind") == "bundle" and it.get("title") else []
    cat, prod = _suggest((it.get("path") or []) + own, idx)
    return cat, prod, bool(prod)


def category_of(product_keys: str) -> str | None:
    first = next((k for k in (product_keys or "").split(",") if k.strip()), "").strip()
    for c in catalog.load_catalog()["categories"]:
        if any(p["key"] == first for p in c.get("products", [])):
            return c["key"]
    return None


def _recent(at: str | None) -> bool:
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return False
    return age.days < _RECENT_DAYS


_VERSION = re.compile(r"[-_ ]v\.?\s?\d[\w.]*|[-_ ]\d+(?:\.\d+)+$|[-_ ]\d{6,8}[-_]\d{6}\b", re.I)


def _title_key(name: str) -> str:
    """A document title without what changes between copies of it: the
    extension, version ("-v7", "v.1", "_1.0.50"), export dates
    ("-170925-134345"), a "CS-" prefix and a browser's " 2" or "(1)"."""
    n = re.sub(r"\.(pdf|docx|txt)$", "", (name or "").strip(), flags=re.I).lower()
    n = re.sub(r"^cs[-_ ]", "", n)
    for _ in range(3):                      # "-v2-20250224_144232" is two in a row
        n = _VERSION.sub(" ", n).strip()
    n = re.sub(r"\s*\(?\d\)?$", "", n)
    return re.sub(r"[^a-z0-9+]", "", n)


def _uploads(items: dict) -> dict[str, str]:
    """title key -> the indexed document of that title that this sync did
    not put there (an upload)."""
    try:
        import docstore
        names = [e.get("source") or "" for e in docstore.inventory()]
    except Exception as exc:
        logger.warning("Confluence sync: uploads not read (%s)", exc)
        return {}
    ours = {it.get("source") for it in items.values() if it.get("synced_version") is not None}
    out: dict[str, str] = {}
    for src in names:
        if src and src not in ours and _title_key(src):
            out.setdefault(_title_key(src), src)
    return out


def _matches(items: dict, uploads: dict | None = None) -> dict[str, list[tuple[str, bool]]]:
    """item key -> [(an upload it is a copy of, whether it is the whole
    manual and so can replace it)]. One item per upload: a combined
    document before a file before a page. A combined document is also the
    copy of an upload named after one of its sections ("MyConnect Quick
    Start Guide" inside "MyConnect Environment")."""
    uploads = _uploads(items) if uploads is None else uploads
    if not uploads:
        return {}
    # Pages that have pages under them. A file's path ends with the page it
    # is attached to, which a file does not make a chapter of anything.
    folders = {tuple((it.get("path") or [])[:k]) for it in items.values()
               for k in range(1, len(it.get("path") or []) + (0 if it.get("kind") == "file" else 1))}
    best: dict[str, tuple] = {}
    rank = {"bundle": 0, "file": 1, "page": 2}
    for key, it in items.items():
        if not it.get("present", True):
            continue
        # A page with chapters under it is only the cover of the manual.
        whole = it.get("kind") != "page" or tuple((it.get("path") or []) + [it.get("title")]) not in folders
        titles = [(it.get("title") or "", 0)]
        if it.get("kind") == "bundle":
            titles += [(m[1].split(" › ")[-1], 1) for m in it.get("members") or []]
        for title, section in titles:
            src = uploads.get(_title_key(title))
            if not src:
                continue
            cand = (not whole, section, rank.get(it.get("kind"), 3), key)
            if src not in best or cand < best[src][0]:
                best[src] = (cand, key, whole)
    out: dict[str, list] = {}
    for src, (_, key, whole) in sorted(best.items(), key=lambda kv: kv[1][0]):
        out.setdefault(key, []).append((src, whole))
    return out


def overview() -> dict:
    """Everything the console's Confluence page shows."""
    st = _load()
    filed = _filed_all()
    idx = _name_index()
    matches = _matches(st["items"])
    keep = set(st.get("keep_uploads") or [])
    items = []
    for key, it in st["items"].items():
        row = {k: it.get(k) for k in ("kind", "title", "path", "url", "size", "version",
                                      "decision", "source", "synced_at", "error", "present",
                                      "modified_at", "first_seen_at", "updated_at", "prev_version",
                                      "pages")}
        row["is_new"] = _recent(it.get("first_seen_at"))
        row["is_updated"] = _recent(it.get("updated_at"))
        row["key"] = key
        row["status"] = status_of(it)
        if it.get("synced_version") is not None:
            row["product"] = filed.get(it["source"], "")   # the catalogue is the truth once synced
        else:
            _, row["product"], row["suggested"] = _effective(it, idx)
        if key in matches:
            (row["uploaded_as"], whole), more = matches[key][0], matches[key][1:]
            row["also_uploaded"] = [u for u, _ in more]
            row["keep_upload"] = row["uploaded_as"] in keep
            row["pdf_kept"] = any(w and u not in keep and _pdf_first(it, u) and _held(u)
                                  for u, w in matches[key])
            row["replaces"] = whole and not row["keep_upload"] and not row["pdf_kept"]
            # Filed as the upload was, unless someone chose otherwise.
            if row["replaces"] and row.get("suggested") is not False and it.get("synced_version") is None \
                    and filed.get(row["uploaded_as"]) and not it.get("product_chosen"):
                row["product"], row["suggested"] = filed[row["uploaded_as"]], True
        items.append(row)
    items.sort(key=lambda r: ([p.lower() for p in r["path"] or []], (r["title"] or "").lower()))
    return {"mode": st["mode"], "checked_at": st.get("checked_at"),
            "bundles": [b["path"] for b in st["bundles"]],
            "items": items, "history": st["history"][::-1],
            "running": running(), "configured": configured(),
            "schedule": schedule(st), "next_run": _next_run(st)}


# ── decisions from the console ──────────────────────────────────────────

def set_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError("mode must be review or auto")
    def change(st):
        st["mode"] = mode
        if mode == "auto":
            for it in st["items"].values():
                it.pop("held", None)
        else:   # edits found while in auto and not yet synced must not go out unseen
            for it in st["items"].values():
                if status_of({**it, "error": None}) == "changed":
                    it["held"] = True
    _update(change)


def bundle_key(b: dict) -> str:
    """The item a combined folder is listed as: by its Confluence id, so a
    rename or move in Confluence keeps the same item (and source); by its
    titles only until a check has found the id."""
    return "bundle:id:" + b["id"] if b.get("id") else "bundle:" + "\u0001".join(b["path"])


def set_bundle(path: list[str], on: bool) -> None:
    """Combine the folder at `path` (titles from the root down) into one
    document, or split it again. Takes effect at the next check. Splitting
    hands the combined document's decision and product to its pages."""
    path = [str(t) for t in path if str(t).strip()]
    if not path:
        raise ValueError("choose a folder")

    def change(st):
        mine = [b for b in st["bundles"] if b["path"] == path]
        st["bundles"] = [b for b in st["bundles"] if b["path"] != path]
        # A folder someone split is never combined again on its own.
        # ponytail: remembered by titles, so a split folder renamed later
        # can be recommended again; key by id if that turns up.
        st["split"] = [p for p in st.get("split", []) if p != path] + ([] if on else [path])
        if on:
            st["bundles"] += mine or [{"path": path}]
        else:
            its = [st["items"][bundle_key(b)] for b in mine if bundle_key(b) in st["items"]]
            it = its[0] if its else {}
            st.setdefault("unbundled", {})["\u0001".join(path)] = {
                "decision": it.get("decision"), "product": it.get("product"),
                "category": it.get("category"), "chosen": it.get("product_chosen")}
    _update(change)


_ONE_MANUAL = re.compile(r"(manual|manual de usuario|guide|specification|handbook)$", re.I)


def _recommend(st: dict) -> list[list[str]]:
    """Folders (or pages with chapters) to combine without being asked: one
    manual kept as a page per chapter, which answers worse in pieces. Its
    title names a single manual ("NV200 Range User Manual", not "Software
    Manuals"), it holds at least 3 pages, and they all have one decision and
    one product, so combining changes no one's choice ("Software Manual"
    holds tools for two products and stays as it is)."""
    filed = _filed_all()
    split = st.get("split", [])
    taken = [b["path"] for b in st["bundles"]] + split
    under: dict[tuple, list] = {}
    for it in st["items"].values():
        if it.get("kind") != "page" or not it.get("present", True):
            continue
        full = tuple(it.get("path") or []) + (it.get("title"),)
        prod = filed.get(it.get("source"), "") if it.get("synced_version") is not None \
            else it.get("product") or ""
        for k in range(1, len(full) + 1):
            under.setdefault(full[:k], []).append((it.get("decision"), prod))
    picks = [list(n) for n, pages in under.items()
             if len(pages) >= 3 and len(set(pages)) == 1 and _ONE_MANUAL.search(n[-1].strip())
             and not any(list(n[:len(t)]) == t for t in taken)
             and not any(s[:len(n)] == list(n) for s in split)]
    return [p for p in picks if not any(q != p and p[:len(q)] == q for q in picks)]   # outermost


def exclude_removed(source: str) -> int:
    """A synced document removed on the Documents page: mark it Excluded and
    not synced, so the Confluence page says what the bot holds and the next
    edit in Confluence does not quietly add it back. Returns items changed."""
    n = 0

    def change(st):
        nonlocal n
        for it in st["items"].values():
            if it.get("source") == source and it.get("synced_version") is not None:
                it.update(decision="excluded", synced_version=None, synced_at=None)
                it.pop("error", None)
                n += 1
    _update(change)
    return n


def set_items(keys: list[str], decision: str | None = None,
              product: str | None = None, keep_upload: bool | None = None) -> None:
    """Approve, mark internal, exclude or re-file items. Decisions take
    effect at the next sync, except between approved and internal for a
    document already synced, which is a flag and takes effect now; so does a
    new product for a synced document."""
    if decision is not None and decision not in DECISIONS:
        raise ValueError("decision must be approved, internal or excluded")
    retag, flag = [], []
    matches = _matches(_load()["items"]) if keep_upload is not None or decision else {}

    def change(st):
        for k in keys:
            it = st["items"].get(k)
            if not it:
                continue
            if decision:
                it["decision"] = decision
                it.pop("error", None)
                it.pop("held", None)        # a person has now seen this version
                if decision != "excluded":
                    it["approved_version"] = it.get("version")    # the version the person saw
                if it.get("synced_version") is not None and decision != "excluded":
                    # Every name the manual is held under: the copy, the PDF
                    # kept over it, uploads waiting to be replaced (one manual, one mark).
                    flag.extend(_copies(it, matches.get(k, []), set(st.get("keep_uploads") or [])))
            if keep_upload is not None:
                kept = set(st.get("keep_uploads") or [])
                for upload, _ in matches.get(k, []):
                    (kept.add if keep_upload else kept.discard)(upload)
                st["keep_uploads"] = sorted(kept)
                # "Keep both" on a manual whose PDF was kept: the copy goes in at the next sync.
                if keep_upload and it.pop("pdf_kept", None):
                    it["synced_version"] = None
            if product is not None:
                if it.get("synced_version") is not None:
                    retag.append(it.get("pdf_kept") or it["source"])
                else:
                    it["product"], it["category"] = product, category_of(product)
                    it["product_chosen"] = True
    _update(change)
    if flag:
        on = decision == "internal"
        # A mark goes only on a name the bot holds (a PDF kept over the copy
        # leaves the copy's name unheld); a sync marks the copy before ingest.
        flag = [s for s in dict.fromkeys(flag) if not on or _held(s)]
        if flag:
            _mark_internal(flag, on)
    if retag:
        from routes_documents import retag_source
        cat = category_of(product) or ""      # ingest's own "unassigned"
        for src in retag:
            retag_source(src, cat, product)


# ── Confluence ──────────────────────────────────────────────────────────

class _Api:
    def __init__(self, site: str, email: str, token: str):
        r = requests.get(f"https://{site}/_edge/tenant_info", timeout=20)
        r.raise_for_status()
        self.site = site
        self.root = "https://api.atlassian.com/ex/confluence/" + r.json()["cloudId"]
        self.http = requests.Session()
        self.http.auth = (email, token)

    def get(self, path: str, **params) -> requests.Response:
        # A check makes over a thousand requests; one slow or busy answer
        # from Atlassian must not throw the whole run away (it did, at
        # request ~900 of the first combined check). Four tries, backing off.
        for attempt in range(4):
            try:
                r = self.http.get(self.root + path, params=params, timeout=60)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 3:
                    raise
                time.sleep(3 * 2 ** attempt)
                continue
            if r.status_code in (429, 502, 503, 504) and attempt < 3:
                wait = r.headers.get("Retry-After", "")
                time.sleep(min(60, int(wait)) if wait.isdigit() else 3 * 2 ** attempt)
                continue
            break
        if r.status_code in (401, 403):
            raise PermissionError(
                f"Confluence refused {path.split('?')[0]} ({r.status_code}): check the "
                "account email, the token, its scopes, and that the account can see the folder")
        r.raise_for_status()
        return r

    def each(self, path: str, **params):
        """Every result of a v2 list, following its cursor links."""
        while path:
            j = self.get(path, **params).json()
            yield from j.get("results", [])
            path, params = (j.get("_links") or {}).get("next"), {}

    def page(self, pid: str) -> dict:
        return self.get(f"/wiki/api/v2/pages/{pid}", **{"body-format": "storage"}).json()

    def attachment(self, att_id: str) -> dict:
        return self.get(f"/wiki/api/v2/attachments/{att_id}").json()

    def download(self, page_id: str, att_id: str) -> bytes:
        return self.get(f"/wiki/rest/api/content/{page_id}/child/attachment/{att_id}/download").content


def _connect() -> tuple["_Api", str, str]:
    s = keystore.get_confluence_settings()
    site, root_kind, root_id = parse_folder_url(s["folder_url"])
    return _Api(site, s["email"], keystore.get_confluence_token()), root_kind, root_id


def _walk(api: _Api, root_kind: str, root_id: str):
    """(page, titles of the folders and pages above it, their ids) for every
    page under the root, depth first in Confluence's own order, and the root
    itself when it is a page (its title is read with its body)."""
    if root_kind == "pages":
        yield {"id": root_id, "title": ""}, [], []
    stack = [(root_kind, root_id, [], [], None)]
    while stack:
        kind, node, trail, ids, me = stack.pop()
        if me is not None:
            yield me, trail, ids
        below, below_ids = (trail + [me["title"]], ids + [me["id"]]) if me is not None else (trail, ids)
        kids = list(api.each(f"/wiki/api/v2/{kind}/{node}/direct-children", limit=250))
        for c in reversed(kids):          # pushed backwards, so they come off in order
            if c.get("type") == "page":
                stack.append(("pages", c["id"], below, below_ids, c))
            elif c.get("type") == "folder":
                stack.append(("folders", c["id"], below + [c["title"]], below_ids + [c["id"]], None))


_HARD = "\x00"        # a block boundary, made a blank line by page_text


class _Text(HTMLParser):
    """Confluence storage format to plain text: paragraphs on consecutive
    lines, a blank line only at a heading or a table, tables fenced as the PDF
    parser fences them, a table row on one line with its cells split by " | "
    (tables.cells), macro parameters dropped."""
    _BLOCK = {"p", "div", "br", "li", "tr", "table", "pre", "blockquote",
              "h1", "h2", "h3", "h4", "h5", "h6"}
    # Parameters and ADF attributes are settings, not text: they put
    # "Scroll Pagebreak...ari:cloud:ecosystem::extension/..." and a note
    # panel's id into the manuals. An ADF fallback repeats its node's content.
    _SKIP = {"ac:parameter", "ac:adf-parameter", "ac:adf-attribute", "ac:adf-fallback"}
    _HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__()
        self.out, self.skip, self.cell, self.first = [], 0, 0, True
        # Imported here: chunking pulls in text_utils, which the gate tests stub.
        from chunking import TABLE_CLOSE, TABLE_OPEN
        self.fence = (TABLE_OPEN, TABLE_CLOSE)

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.skip += 1
        elif tag in ("td", "th"):
            # Each cell's <p> used to break the line, so a row came out one
            # cell per line under a lone "|" -- 572 of 619 pages (2026-10-23).
            self.out.append("" if self.first else " | ")
            self.first, self.cell = False, self.cell + 1
        elif tag == "table" and not self.cell:
            # Fenced as the PDF parser fences its tables: the chunker keeps a
            # table whole, or splits it by row repeating the header.
            self.out.append(f"{_HARD}{self.fence[0]}\n")
        elif tag in self._HEADINGS and not self.cell:
            self.out.append(_HARD)
        elif tag in self._BLOCK:
            self.out.append(" " if self.cell and tag != "tr" else "\n")
            if tag == "tr":
                self.first = True

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in ("td", "th"):
            self.cell = max(0, self.cell - 1)
        elif tag == "tr":
            pass                        # the next row's <tr> breaks the line
        elif tag == "table" and not self.cell:
            self.out.append(f"\n{self.fence[1]}{_HARD}")
        elif tag in self._HEADINGS and not self.cell:
            self.out.append(_HARD)
        elif tag in self._BLOCK:
            self.out.append(" " if self.cell else "\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)

    def unknown_decl(self, data):       # code macros keep their body in CDATA
        if data.startswith("CDATA["):
            self.out.append(data[6:])


def page_text(title: str, storage_html: str) -> str:
    p = _Text()
    p.feed(storage_html or "")
    body = re.sub(r"[ \t]+\n", "\n", "".join(p.out))
    body = "\n".join(re.sub(r"\s{2,}", " ", l).strip() if " | " in l else l
                     for l in body.split("\n"))    # a cell's <p> leaves spaces
    # Paragraphs on consecutive lines, a blank line only at a heading or a
    # table: a blank line after every <p> made each one a block of its own,
    # which the chunker joins into one long line (a guide lost its steps).
    body = re.sub(r"\n\s*\n", "\n", body)
    body = re.sub(r"\s*\x00[\s\x00]*", "\n\n", body).strip()
    return f"{title}\n\n{body}" if body else ""


def _body(full: dict) -> str:
    return ((full.get("body") or {}).get("storage") or {}).get("value") or ""


def _safe_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "-", title).strip(" .") or "untitled"


def _link(api: _Api, webui: str | None) -> str | None:
    return f"https://{api.site}/wiki{webui}" if webui else None


# ── check ───────────────────────────────────────────────────────────────

def _stamp(parts: list[str]) -> str:
    """A combined document's version: any page added, removed or edited changes it."""
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


def _check(api: _Api, root_kind: str, root_id: str) -> dict:
    """List the root into the state's items. Indexes nothing."""
    found: dict[str, dict] = {}

    def recommend(st):
        # Read from the last check's list, so a manual found for the first
        # time is combined at the check after.
        for path in _recommend(st):
            st["bundles"].append({"path": path})
            logger.info("Confluence sync: combining %s, one manual in pages", " / ".join(path))
    bundles = _update(recommend)["bundles"]
    ids_in = {b["id"] for b in bundles if b.get("id")}     # combined folders by Confluence id
    by_path = [b for b in bundles if not b.get("id")]      # still known only by titles
    pinned: dict[str, str] = {}          # title-path bundle key -> the id found there
    paths: dict[str, list] = {}          # id -> its titles now, for the console
    combined: dict[str, dict] = {}       # bundle key -> the item being built
    members_of: set[str] = set()         # page items now inside a combined one
    for page, trail, ids in _walk(api, root_kind, root_id):
        _stop_point()
        pid = page["id"]
        _progress.update(phase="checking", done=len(found))
        full = api.page(pid)
        title = full.get("title") or page["title"]
        here, here_ids = trail + [title], ids + [pid]
        if pid != root_id:
            for b in [b for b in by_path if here[:len(b["path"])] == b["path"]]:
                by_path.remove(b)
                pinned[bundle_key(b)] = here_ids[len(b["path"]) - 1]
                ids_in.add(pinned[bundle_key(b)])
        # The innermost combined folder above the page (or the page itself).
        deep = max((i for i, c in enumerate(here_ids) if c in ids_in), default=None) \
            if pid != root_id else None
        inside = here[:deep + 1] if deep is not None else None
        if inside is not None:
            text = page_text(title, _body(full))
            key = bundle_key({"id": here_ids[deep]})
            paths.setdefault(here_ids[deep], inside)
            b = combined.get(key)
            if b is None:
                b = combined[key] = {
                    "kind": "bundle", "id": key, "title": inside[-1], "path": inside[:-1],
                    "name": _safe_name(inside[-1]) + ".txt", "members": [], "stamp": [],
                    "modified_at": None, "url": None}
                _note(inside[-1], "bundle", inside[:-1], "found")
            if here == inside:           # the folder is itself a page: link to it
                b["url"] = _link(api, (full.get("_links") or {}).get("webui"))
            ver = (full.get("version") or {}).get("number")
            members_of.add(f"page:{pid}")
            if text:
                b["members"].append([pid, " › ".join(here[len(inside):]) or title])
                b["stamp"].append(f"{pid}:{ver}")
                at = (full.get("version") or {}).get("createdAt")
                if at and (not b["modified_at"] or at > b["modified_at"]):
                    b["modified_at"] = at
        # The root page's own text is the container's intro, not a manual;
        # files attached to it still count.
        elif pid != root_id and len(page_text(title, _body(full))) >= _MIN_PAGE_TEXT:
            found[f"page:{pid}"] = {
                "kind": "page", "id": pid, "title": title, "path": trail,
                "name": _safe_name(title) + ".txt",
                "version": (full.get("version") or {}).get("number"),
                "modified_at": (full.get("version") or {}).get("createdAt"),
                "url": _link(api, (full.get("_links") or {}).get("webui"))}
            _note(title, "page", trail, "found")
        for att in api.each(f"/wiki/api/v2/pages/{pid}/attachments", limit=250):
            name = _safe_name(att.get("title") or "")
            if not name.lower().endswith(_FILE_TYPES):
                continue
            found[f"att:{att['id']}"] = {
                "kind": "file", "id": att["id"], "page_id": pid, "title": att.get("title"),
                "path": trail + ([title] if title else []), "name": name,
                "version": (att.get("version") or {}).get("number"),
                "modified_at": (att.get("version") or {}).get("createdAt"),
                "size": att.get("fileSize"),
                "url": _link(api, att.get("webuiLink"))}
            _note(att.get("title") or name, "file", trail + ([title] if title else []), "found")

    for key, b in combined.items():
        if b["members"]:
            # Its version is its pages': any page added, removed or edited changes it.
            b["version"] = _stamp(b.pop("stamp"))
            b["pages"] = len(b["members"])
            found[key] = b

    res = {"at": _now(), "kind": "check", "found": len(found), "new": [], "changed": [], "gone": []}

    def merge(st):
        items = st["items"]
        # Combined folders pinned to their ids, with their titles as found now,
        # so a rename or move of the folder (or one above it) keeps them.
        kept, seen = [], set()
        for b in st["bundles"]:
            old = bundle_key(b)
            cid = b.get("id") or pinned.get(old)
            if cid in seen:              # combined twice, by id and by titles
                continue
            if cid:
                seen.add(cid)
                b = {"id": cid, "path": paths.get(cid, b["path"])}
                if old != bundle_key(b) and old in items and bundle_key(b) not in items:
                    items[bundle_key(b)] = items.pop(old)
            elif b in by_path:           # not one combined while this check ran
                logger.warning("Confluence sync: combined folder %s not found by its titles; kept",
                               " / ".join(b["path"]))
            kept.append(b)
        st["bundles"] = kept
        # The first check finds everything; calling all of it "new" says nothing.
        first_check = not st.get("checked_at")
        taken = {it["source"] for k, it in items.items() if it.get("source") and k in found}
        held = None                      # the catalogue's filing, read once if needed
        for key, f in found.items():
            it = items.get(key)
            if it is None:
                cat, prod = _suggest(f["path"] + ([f["title"]] if f["kind"] == "bundle" else []))
                decision = "excluded"
                if st["mode"] == "auto":
                    decision = "internal" if _INTERNAL_TITLE.search(f["title"] or "") else "approved"
                chosen = False
                if f["kind"] == "bundle":
                    pages = [items[f"page:{m[0]}"] for m in f["members"] if f"page:{m[0]}" in items]
                    # Combining approved pages keeps them approved.
                    if any(pg.get("decision") == "approved" for pg in pages):
                        decision = "approved"
                    elif pages and len({pg.get("decision") for pg in pages}) == 1:
                        decision = pages[0].get("decision")      # all internal stay internal
                    # ...and pages filed alike keep that filing, chosen or in the catalogue.
                    held = held if held is not None else _filed_all()
                    filed = {held.get(pg.get("source"), "") if pg.get("synced_version") is not None
                             else pg.get("product") for pg in pages
                             if pg.get("product_chosen") or pg.get("synced_version") is not None}
                    if len(filed) == 1 and next(iter(filed)):
                        prod = next(iter(filed))
                        cat, chosen = category_of(prod), True
                else:
                    # Split from a combined document: its decision and product carry over.
                    for prefix, was in (st.get("unbundled") or {}).items():
                        b = prefix.split("\u0001")
                        if (f["path"] + [f["title"]])[:len(b)] == b and was.get("decision"):
                            decision = was["decision"]
                            if was.get("product") or was.get("chosen"):
                                cat, prod, chosen = was.get("category"), was.get("product") or "", True
                it = items[key] = {"decision": decision, "category": cat, "product": prod,
                                   "product_chosen": chosen, "synced_version": None,
                                   "first_seen_at": None if first_check else res["at"]}
                res["new"].append(f["title"])
            elif it.get("version") is not None and it["version"] != f["version"]:
                it["updated_at"], it["prev_version"] = res["at"], it["version"]
                res["changed"].append(f["title"])
                if st["mode"] == "review" and it.get("decision") != "excluded":
                    it["held"] = True
            name = f.pop("name")
            if not it.get("source"):
                # Two documents of the same name: the second gets its id.
                if name in taken:
                    stem, ext = os.path.splitext(name)
                    name = f"{stem} ({f['id']}){ext}"
                it["source"] = name
                taken.add(name)
            it.update(f, present=True)
            it.pop("combined", None)
        # Pages now inside a combined document: removed at the next sync if
        # they were synced on their own, dropped from the list if not.
        for key in members_of:
            it = items.get(key)
            if it is None:
                continue
            if it.get("synced_version") is None:
                del items[key]
            else:
                it.update(present=False, combined=True)
        for key, it in items.items():
            if key not in found and it.get("present", True):
                it["present"] = False
                res["gone"].append(it.get("title"))
        # Gone and never synced: nothing to undo, so nothing to show.
        for key in [k for k, it in items.items()
                    if not it["present"] and it.get("synced_version") is None]:
            del items[key]
        st["checked_at"] = res["at"]
        st["found_nothing"] = not found
        st.pop("unbundled", None)
    _update(merge)
    return res


# ── apply ───────────────────────────────────────────────────────────────

def _union(*keys: str) -> str:
    """"a,b" + "b,c" -> "a,b,c", order kept."""
    return ",".join(dict.fromkeys(k.strip() for s in keys for k in (s or "").split(",") if k.strip()))


def _indexed(source: str) -> bool:
    from db import get_collection
    return bool(source) and bool(get_collection().get(where={"source": source}, limit=1).get("ids"))


def _marked(source: str) -> bool:
    from retrieval_db import is_marked_internal
    return is_marked_internal(source)


def _stored(source: str) -> bool:
    import docstore
    return bool(source) and docstore.find(source) is not None


def _held(source: str) -> bool:
    """The bot holds it: answering from it, or keeping it internal. Since
    D-20261024-06 a marked document has no chunks, so _indexed alone reads
    "marked internal" as "gone"."""
    return _indexed(source) or (_marked(source) and _stored(source))


def _copies(it: dict, pairs: list, keep: set) -> list[str]:
    """Every name one manual can be held under: the Confluence copy, the PDF
    kept over it, and each upload it is a whole copy of. Not an upload kept
    beside it ("Keep both" is two documents, each with its own mark)."""
    return list(dict.fromkeys(n for n in [it.get("source"), it.get("pdf_kept"),
                                          *(u for u, whole in pairs if whole and u not in keep)] if n))


def items_holding(sources: list[str]) -> dict[str, list[str]]:
    """key -> every name of each synced item holding any of `sources` under one of its names."""
    st = _load()
    matches, keep = _matches(st["items"]), set(st.get("keep_uploads") or [])
    want, out = set(sources), {}
    for key, it in st["items"].items():
        if it.get("synced_version") is None:
            continue
        names = _copies(it, matches.get(key, []), keep)
        if want & set(names):
            out[key] = names
    return out


def _carry_marks(st: dict, matches: dict) -> None:
    """One manual, one mark. A manual held under several names (its copy,
    the PDF kept over it, uploads it replaces) is internal under all of them when
    its item is decided internal or any held name is marked: the mark then
    goes on every held name and the item is decided internal, here and in
    `st`, so nothing this sync does next puts a public copy beside it."""
    if _marked("?"):
        # No file can be named "?": the marks file is unreadable and every
        # name reads as marked (fail closed). Not written into the decisions.
        return
    marks, flips = [], []
    keep = set(st.get("keep_uploads") or [])
    for key, it in st["items"].items():
        if not it.get("present", True) or it.get("decision") == "excluded":
            continue
        names = _copies(it, matches.get(key, []), keep)
        internal = it.get("decision") == "internal"
        if not internal and not any(_marked(n) for n in names):
            continue                                    # the common case: no store reads
        held = [n for n in names if _held(n)]
        if not internal and not any(_marked(n) for n in held):
            continue                                    # a stale mark on a name not held
        marks += [n for n in held if not _marked(n)]
        if not internal:
            flips.append(key)
    if marks:
        _mark_internal(list(dict.fromkeys(marks)), True)
    if flips:
        def flip(s):
            for k in flips:
                if k in s["items"]:
                    s["items"][k]["decision"] = "internal"
        _update(flip)
        for k in flips:
            st["items"][k]["decision"] = "internal"


def _retag(source: str, cat: str | None, prod: str) -> None:
    from routes_documents import retag_source
    retag_source(source, cat or "", prod)


def _original(source: str) -> bytes | None:
    import docstore
    path = docstore.find(source)
    if not path:
        return None
    with open(path, "rb") as f:
        return f.read()


def _pdf_first(it: dict, upload: str) -> bool:
    """An uploaded PDF beats a Confluence copy that is not a PDF: a page
    converted to text loses its pages, sections, figures and table rows
    (2026-10-24, the weekly 40: text copies 85.3, the PDFs 87.5)."""
    return upload.lower().endswith(".pdf") and not (it.get("source") or "").lower().endswith(".pdf")


def _keep_pdf(it: dict, upload: str, prod: str | None = None, restore: bool = False) -> bool:
    """The uploaded PDF stays the one copy and the Confluence text copy
    goes; the PDF is filed under both copies' products (and `prod`). False,
    doing nothing, when the PDF is not in the index and is not to be
    restored from its stored original."""
    copy = it.get("source") or ""
    prod = _union(_filed(upload)[1], _filed(copy)[1], prod or "")
    if it.get("decision") == "internal" or _marked(copy):
        # Before any ingest: restore_pdfs runs outside _apply's _carry_marks,
        # and a marked PDF is stored, never indexed.
        _mark_internal([upload], True)
    if not _held(upload):
        content = _original(upload) if restore else None
        if content is None:
            return False
        _ingest(content, upload, category_of(prod), prod or None)
    if prod and set(prod.split(",")) != set(_filed(upload)[1].split(",")):
        _retag(upload, category_of(prod), prod)
    if _held(copy):
        _remove(copy)
    return True


def _ingest(content: bytes, source: str, cat: str | None, prod: str | None) -> None:
    from ingest import ingest_file
    ingest_file(content, source, category_key=cat, product_key=prod,
                replace_existing=True)


def _mark_internal(sources: list[str], on: bool) -> None:
    from retrieval_db import set_internal
    set_internal(sources, on)


def _remove(source: str) -> None:
    """What the console's Remove button does, plus the catalogue entry."""
    # Not the /delete_source route: it asks for a sign-in a background sync
    # does not have, which failed every replacement with "401: Sign-in
    # required" after the Confluence copy was already in.
    from routes_documents import remove_source_now
    remove_source_now(source)
    _mark_internal([source], False)
    catalog.refile_source(source, [])


def _bundle_text(api: _Api, it: dict) -> tuple[str, str]:
    """A combined folder as one text: its title, then each page under its
    own section heading (the path below the folder), which the chunker keeps
    as a hard boundary and stores as the chunk's section. Also returns the
    version stamp of the pages as read now."""
    from chunking import SECTION_CLOSE, SECTION_OPEN
    parts = [it["title"]]
    stamp: list[str] = []
    for n, (pid, heading) in enumerate(it.get("members") or []):
        _stop_point()
        _progress.update(page=n + 1, pages=len(it["members"]))
        full = api.page(pid)
        text = page_text(full.get("title") or "", _body(full))
        if text:
            parts.append(f"{SECTION_OPEN}{heading}{SECTION_CLOSE}\n{text}")
            stamp.append(f"{pid}:{(full.get('version') or {}).get('number')}")
    _progress.pop("page", None)
    _progress.pop("pages", None)
    return "\n\n".join(parts), _stamp(stamp)


def _apply(api: _Api) -> dict:
    st = _load()
    res = {"at": _now(), "kind": "sync", "added": [], "updated": [], "removed": [],
           "errors": []}
    matches = _matches(st["items"])
    keep = set(st.get("keep_uploads") or [])
    # Before the todo copies are taken, so they carry the decision.
    _carry_marks(st, matches)
    todo = [(k, dict(it)) for k, it in st["items"].items()
            if status_of({**it, "error": None}) in ("new", "changed", "gone", "remove", "combined")
            # its PDF was kept and has since been removed: the copy answers again
            or (it.get("pdf_kept") and status_of(it) == "synced" and not _held(it["pdf_kept"]))]
    # Removals first: a combined document takes its container page's name,
    # so the page's own copy must be gone before the combined one goes in.
    _removing = ("gone", "remove", "combined")
    todo.sort(key=lambda kv: status_of({**kv[1], "error": None}) not in _removing)
    if st.get("found_nothing") and st["items"]:
        # Nothing listed at all is a permissions change or an emptied folder
        # far more often than a real removal of every manual.
        res["errors"].append("Confluence listed nothing, so nothing was removed. "
                             "Check the account can still see the folder.")
        todo = [(k, it) for k, it in todo if it.get("present", True)]

    res["replaced"] = []
    res["kept_pdf"] = []
    res["held"] = [it["source"] for it in st["items"].values() if status_of({**it, "error": None}) == "held"]
    for n, (key, it) in enumerate(todo):
        if _cancel.is_set():
            res["cancelled"], res["left"] = True, len(todo) - n
            break
        _progress.update(phase="syncing", done=n, total=len(todo))
        src, what = it["source"], status_of({**it, "error": None})
        try:
            pdfs = [] if what in ("gone", "remove", "combined") else \
                [u for u, whole in matches.get(key, [])
                 if whole and u not in keep and _pdf_first(it, u) and _held(u)]
            if what in ("gone", "remove", "combined"):
                _remove(src)
                res["removed"].append(src)
                _note(it.get("title") or src, it["kind"], it.get("path") or [], "removed")
                change = {"synced_version": None, "synced_at": None}
            elif pdfs:
                # The PDF answers; the Confluence copy is not read or indexed.
                for u in pdfs:
                    _keep_pdf(it, u, it.get("product") if it.get("product_chosen") else None)
                    res["kept_pdf"].append(f"{u} kept over {src}")
                    _note(it.get("title") or src, it["kind"], it.get("path") or [], "PDF kept")
                change = {"synced_version": it.get("version"), "synced_at": _now(),
                          "approved_version": None, "pdf_kept": pdfs[0]}
            else:
                if it["kind"] == "bundle":
                    text, version = _bundle_text(api, it)
                    content = text.encode("utf-8")
                elif it["kind"] == "page":
                    full = api.page(it["id"])
                    content = page_text(full.get("title") or it["title"], _body(full)).encode("utf-8")
                    version = (full.get("version") or {}).get("number")
                else:
                    # ponytail: version read just before the download; an edit in that second slips through. Pin the download to the version if it ever matters.
                    # Auto mode needs no fresh version: one request fewer, nothing new to fail.
                    version = (api.attachment(it["id"]).get("version") or {}).get("number") \
                        if st["mode"] == "review" else it.get("version")
                    if int(it.get("size") or 0) > MAX_FILE_BYTES:
                        raise ValueError(f"{int(it['size']) // 1048576} MB, over the "
                                         f"{MAX_FILE_BYTES // 1048576} MB limit; not downloaded")
                    content = api.download(it["page_id"], it["id"])
                    if len(content) > MAX_FILE_BYTES:     # Confluence listed no size
                        raise ValueError(f"over the {MAX_FILE_BYTES // 1048576} MB limit; not indexed")
                # Only the version a person approved goes out in review mode;
                # edited again since, it waits for approval again.
                seen = it.get("approved_version") or it.get("version")
                if st["mode"] == "review" and None not in (seen, version) and version != seen:
                    raise _Moved
                # Not the copy's own name (a new page named like an upload:
                # removing it would take out what was just ingested), and not
                # a PDF only on disk (restore_pdfs' to bring back; Remove deletes it).
                uploads = [u for u, whole in matches.get(key, [])
                           if whole and u not in keep and u != src and _held(u)]
                # Both copies' products: the copy kept only its own, and the
                # Linux guide lost the MyCheckr and MyConnect (2026-10-24).
                own = _filed(src)[1] or (it.get("product") or "" if it.get("product_chosen") else "")
                prod = _union(own, *(_filed(u)[1] for u in uploads))
                cat = category_of(prod) if prod else None
                if not prod:
                    cat, prod, _ = _effective(it, _name_index())
                # A product deleted from the catalogue since the document was
                # filed (ICU, 2026-10-23) failed every document filed under it
                # with "unknown product". It is dropped instead, and said so.
                known = {p["key"] for c in catalog.load_catalog()["categories"]
                         for p in c.get("products", [])}
                gone_keys = [k for k in (prod or "").split(",") if k and k not in known]
                if gone_keys:
                    prod = ",".join(k for k in prod.split(",") if k in known)
                    cat = category_of(prod) if prod else None
                    res["errors"].append(f"{src}: product {', '.join(gone_keys)} no longer exists; "
                                         "indexed without it")
                # Marked before it goes in, so not even a moment passes where
                # an internal document can answer a customer.
                _mark_internal([src], it.get("decision") == "internal")
                _ingest(content, src, cat, prod or None)
                filed_now = _filed(src)[1]
                if prod and not filed_now:
                    catalog.attach_source(cat, prod, src)
                elif prod and set(prod.split(",")) != set(filed_now.split(",")):
                    catalog.refile_source(src, prod.split(","))
                for upload in uploads:
                    # Only once the Confluence copy is in, so a failed
                    # download never leaves the manual missing altogether.
                    _remove(upload)
                    res["replaced"].append(f"{upload} -> {src}")
                    _note(it.get("title") or src, it["kind"], it.get("path") or [], "replaced")
                res["added" if what == "new" else "updated"].append(src)
                _note(it.get("title") or src, it["kind"], it.get("path") or [],
                      "added" if what == "new" else "updated")
                change = {"synced_version": version, "synced_at": _now(), "version": version,
                          "approved_version": None, "pdf_kept": None}
            change["error"] = None
        except Cancelled:
            # Stopped while building a combined document: nothing of it
            # was written, so it simply stays pending.
            res["cancelled"], res["left"] = True, len(todo) - n
            break
        except _Moved:
            res["held"].append(src)
            change = {"held": True, "prev_version": seen, "version": version,
                      "updated_at": res["at"], "error": None}
        except Exception as exc:
            logger.warning("Confluence sync: %s failed: %s", src, exc)
            res["errors"].append(f"{src}: {exc}")
            change = {"error": str(exc)}

        def write(st, key=key, change=change):
            if key in st["items"]:
                st["items"][key].update(change)
                if not st["items"][key].get("present", True) and \
                        st["items"][key].get("synced_version") is None:
                    del st["items"][key]
        _update(write)

    # A manual this sync already keeps, uploaded again by hand since: the
    # upload goes, so the bot never reads two copies of it.
    if res.get("cancelled"):
        return res
    done = {k for k, _ in todo}
    kept: dict[str, str] = {}
    for key, it in st["items"].items():
        if key in done or status_of(it) != "synced":
            continue
        for upload, whole in matches.get(key, []):
            if not whole or upload in keep:
                continue
            try:
                if _pdf_first(it, upload):
                    # Uploaded again by hand (or restored): the PDF answers and
                    # the copy goes. A PDF only on disk is an old file: left alone.
                    if _held(upload) and _held(it.get("source") or "") and _keep_pdf(it, upload):
                        kept.setdefault(key, upload)
                        res["kept_pdf"].append(f"{upload} kept over {it.get('source')}")
                        _note(it.get("title") or upload, it["kind"], it.get("path") or [], "PDF kept")
                    continue
                prod = _union(_filed(it.get("source") or "")[1], _filed(upload)[1])
                _remove(upload)
                if prod and set(prod.split(",")) != set(_filed(it.get("source") or "")[1].split(",")):
                    _retag(it["source"], category_of(prod), prod)
                res["replaced"].append(f"{upload} -> {it.get('source')}")
                _note(it.get("title") or upload, it["kind"], it.get("path") or [], "replaced")
            except Exception as exc:
                res["errors"].append(f"{upload}: not removed: {exc}")
    if kept:
        def mark(s):
            for k, u in kept.items():
                if k in s["items"]:
                    s["items"][k]["pdf_kept"] = u
        _update(mark)
    return res


def restore_pdfs(apply: bool = False, products: dict | None = None) -> list[str]:
    """One-off: before the PDF-first rule (2026-10-22) the sync replaced 13
    uploaded PDF manuals with their Confluence text copies; the PDFs are still
    in the document store, out of the index. Lists "pdf <- copy" for each;
    with apply, indexes the PDF again from its stored original, filed under
    both copies' products plus products[pdf], and removes the copy. Run it
    with the backend stopped, dry run first."""
    st = _load()
    keep = set(st.get("keep_uploads") or [])
    out, kept = [], {}
    for key, pairs in _matches(st["items"]).items():
        it = st["items"][key]
        if status_of(it) != "synced":
            continue
        for upload, whole in pairs:
            if not whole or upload in keep or not _pdf_first(it, upload) or _held(upload):
                continue
            out.append(f"{upload} <- {it['source']}")
            if apply and _keep_pdf(it, upload, (products or {}).get(upload), restore=True):
                kept.setdefault(key, upload)
    if kept:
        def mark(s):
            for k, u in kept.items():
                if k in s["items"]:
                    s["items"][k]["pdf_kept"] = u
        _update(mark)
    return out


# ── runs ────────────────────────────────────────────────────────────────

class Busy(RuntimeError):
    pass


# "Sync now" indexes what is approved without reading Confluence again when
# a check is this recent: reading means a request per page (about 1,500 of
# them, 13 minutes or more), which held 24 approved documents back for 50.
RECHECK_AFTER = float(os.getenv("CONFLUENCE_RECHECK_HOURS", "6")) * 3600


def run(apply: bool = True, check: bool | None = None) -> dict:
    """Check, then (unless apply=False) sync. check=None reads Confluence
    first only when the last check is older than RECHECK_AFTER; a sync
    always checks when check=True (the daily run). Returns and records the
    result."""
    if check is None:
        check = not apply or _since_last_check() > RECHECK_AFTER
    if not _run_lock.acquire(blocking=False):
        raise Busy("A Confluence check or sync is already running")
    try:
        _progress.clear()
        _cancel.clear()
        _progress.update(phase="connecting")
        try:
            api, root_kind, root_id = _connect()
            res = _check(api, root_kind, root_id) if check else {}
            if apply:
                res = {**_apply(api), **{k: res[k] for k in ("found", "new", "changed", "gone") if k in res},
                       "checked": check}
        except Cancelled:
            # Stopped during the check: it writes nothing until it finishes,
            # so nothing changed, and the sync after it never started.
            res = {"at": _now(), "kind": "sync" if apply else "check", "cancelled": True}
        except Exception as exc:
            logger.warning("Confluence %s failed: %s", "sync" if apply else "check", exc)
            res = {"at": _now(), "kind": "sync" if apply else "check", "failed": str(exc)}

        # History keeps counts: a first check of 2,000 documents made every
        # entry carry 2,000 titles.
        kept = {k: (len(v) if isinstance(v, list) and k != "errors" else v) for k, v in res.items()}
        kept["errors"] = (res.get("errors") or [])[:50]

        def record(st):
            st["history"] = (st["history"] + [kept])[-_HISTORY:]
        _update(record)
        logger.info("Confluence %s: %s", res["kind"],
                    {k: (len(v) if isinstance(v, list) else v) for k, v in res.items() if k != "at"})
        return res
    finally:
        _progress.clear()
        _cancel.clear()
        _run_lock.release()


def run_in_background(apply: bool = True, check: bool | None = None) -> None:
    def go():
        try:
            run(apply, check)
        except Busy:
            pass                     # a second click while one runs
    threading.Thread(target=go, name="confluence-sync", daemon=True).start()


# ── periodic sync ───────────────────────────────────────────────────────

def _interval_seconds() -> float:
    try:
        return float(os.getenv("CONFLUENCE_SYNC_HOURS", "24")) * 3600
    except ValueError:
        return 24 * 3600


# The automatic sync, set on the Confluence page. "hours": every N hours
# counted from the last check (restarting the server does not re-read the
# folder); "daily"/"weekly": at a time of day, server local time, weekly on
# `day` (0 = Monday). Until someone sets one, CONFLUENCE_SYNC_HOURS (24)
# every-N-hours, which is what it always did.
EVERY = ("off", "hours", "daily", "weekly")


def schedule(st: dict | None = None) -> dict:
    sched = (st or _load()).get("schedule")
    if sched:
        return sched
    hours = _interval_seconds() / 3600
    return {"every": "hours" if hours > 0 else "off", "hours": hours or 24,
            "time": "02:00", "day": 0}


def set_schedule(every: str, hours: float = 24, at: str = "02:00", day: int = 0) -> dict:
    if every not in EVERY:
        raise ValueError("every must be off, hours, daily or weekly")
    if every == "hours" and not 1 <= float(hours) <= 24 * 7:
        raise ValueError("every so many hours: between 1 and 168")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at or ""):
        raise ValueError("time must be HH:MM")
    if not 0 <= int(day) <= 6:
        raise ValueError("day must be 0 (Monday) to 6 (Sunday)")
    sched = {"every": every, "hours": float(hours), "time": at, "day": int(day)}
    _update(lambda st: st.__setitem__("schedule", sched))
    return sched


def _slot(sched: dict, now: datetime) -> datetime:
    """The most recent daily/weekly time at or before `now` (local)."""
    hh, mm = (int(x) for x in sched["time"].split(":"))
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if sched["every"] == "weekly":
        at -= timedelta(days=(at.weekday() - sched["day"]) % 7)
    if at > now:
        at -= timedelta(days=7 if sched["every"] == "weekly" else 1)
    return at


def _next_run(st: dict | None = None) -> str | None:
    st = st or _load()
    sched = schedule(st)
    now = datetime.now().astimezone()
    if sched["every"] == "off":
        return None
    if sched["every"] == "hours":
        wait = max(0.0, sched["hours"] * 3600 - _since_last_check())
        return (now + timedelta(seconds=wait)).isoformat(timespec="minutes")
    nxt = _slot(sched, now) + timedelta(days=7 if sched["every"] == "weekly" else 1)
    last = st.get("auto_run_at")
    if not last or datetime.fromisoformat(last) < _slot(sched, now):
        nxt = _slot(sched, now)          # this slot has not run yet: due now
    return nxt.isoformat(timespec="minutes")


def _due(st: dict, now: datetime) -> bool:
    sched = schedule(st)
    if sched["every"] == "hours":
        return _since_last_check() >= sched["hours"] * 3600
    if sched["every"] in ("daily", "weekly"):
        slot = _slot(sched, now)
        last = st.get("auto_run_at")
        # A slot missed while the server was down runs when it comes back,
        # but only that day's: an old one is not worth a surprise sync.
        return (not last or datetime.fromisoformat(last) < slot) \
            and now - slot < timedelta(hours=12)
    return False


def _loop() -> None:
    from app_state import capability
    time.sleep(300)   # after startup and the model warmup
    while True:
        try:
            st = _load()
            now = datetime.now().astimezone()
            if _due(st, now) and configured() and capability("search") and not running():
                _update(lambda s: s.__setitem__("auto_run_at", now.isoformat(timespec="seconds")))
                run_in_background(apply=True, check=True)
        except Exception as exc:
            logger.warning("scheduled Confluence sync skipped: %s", exc)
        time.sleep(60)


def _since_last_check() -> float:
    at = _load().get("checked_at")
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(at)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def start() -> None:
    threading.Thread(target=_loop, name="confluence-sync-timer", daemon=True).start()
