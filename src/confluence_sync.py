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
anything not approved is excluded. In "auto" mode a newly found document is
approved at once (sync everything); in "review" mode it starts excluded and
marked new until someone approves it on the console's Confluence page. A
document that was approved stays approved when it changes in Confluence.

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
from datetime import datetime, timezone
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
DECISIONS = ("approved", "excluded")
_RECENT_DAYS = 14                  # how long "new" and "updated" flags last

# A folder, or a page whose child pages hold the manuals: both are a
# "Website" container in Confluence, depending on how the space was built.
_FOLDER_URL = re.compile(
    r"^https://([a-z0-9-]+\.atlassian\.net)/wiki/spaces/[^/]+/(folder|pages)/(\d+)", re.I)

_run_lock = threading.Lock()      # one check/sync at a time
_state_lock = threading.Lock()    # read-modify-write of the state file
_progress: dict = {}


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
    st.setdefault("bundles", [])
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
    if not synced:
        return "new"
    return "synced" if it.get("synced_version") == it.get("version") else "changed"


def _filed(source: str) -> tuple[str | None, str]:
    """(category key, "k1,k2") a source is filed under in the catalogue."""
    data = catalog.load_catalog()
    held = [(c["key"], p["key"]) for c in data["categories"]
            for p in c.get("products", []) if source in p.get("sources", [])]
    return (held[0][0] if held else None), ",".join(k for _, k in held)


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
            hit = idx.get(_norm(part))
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
    filed: dict[str, str] = {}             # source -> "k1,k2", one catalogue read
    for c in catalog.load_catalog()["categories"]:
        for p in c.get("products", []):
            for src in p.get("sources", []):
                filed[src] = (filed[src] + "," if src in filed else "") + p["key"]
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
            row["replaces"] = whole and not row["keep_upload"]
            # Filed as the upload was, unless someone chose otherwise.
            if row["replaces"] and row.get("suggested") is not False and it.get("synced_version") is None \
                    and filed.get(row["uploaded_as"]) and not it.get("product_chosen"):
                row["product"], row["suggested"] = filed[row["uploaded_as"]], True
        items.append(row)
    items.sort(key=lambda r: ([p.lower() for p in r["path"] or []], (r["title"] or "").lower()))
    return {"mode": st["mode"], "checked_at": st.get("checked_at"), "bundles": st["bundles"],
            "items": items, "history": st["history"][::-1],
            "running": running(), "configured": configured()}


# ── decisions from the console ──────────────────────────────────────────

def set_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError("mode must be review or auto")
    _update(lambda st: st.__setitem__("mode", mode))


def bundle_key(path: list[str]) -> str:
    return "bundle:" + "\u0001".join(path)


def set_bundle(path: list[str], on: bool) -> None:
    """Combine the folder at `path` (titles from the root down) into one
    document, or split it again. Takes effect at the next check. Splitting
    hands the combined document's decision and product to its pages."""
    path = [str(t) for t in path if str(t).strip()]
    if not path:
        raise ValueError("choose a folder")

    def change(st):
        st["bundles"] = [b for b in st["bundles"] if b != path]
        if on:
            st["bundles"].append(path)
        else:
            it = st["items"].get(bundle_key(path)) or {}
            st.setdefault("unbundled", {})["\u0001".join(path)] = {
                "decision": it.get("decision"), "product": it.get("product"),
                "category": it.get("category"), "chosen": it.get("product_chosen")}
    _update(change)


def set_items(keys: list[str], decision: str | None = None,
              product: str | None = None, keep_upload: bool | None = None) -> None:
    """Approve, exclude or re-file items. Decisions take effect at the next
    sync; a new product for a document already synced takes effect now."""
    if decision is not None and decision not in DECISIONS:
        raise ValueError("decision must be approved or excluded")
    retag = []
    matches = _matches(_load()["items"]) if keep_upload is not None else {}

    def change(st):
        for k in keys:
            it = st["items"].get(k)
            if not it:
                continue
            if decision:
                it["decision"] = decision
                it.pop("error", None)
            if keep_upload is not None:
                kept = set(st.get("keep_uploads") or [])
                for upload, _ in matches.get(k, []):
                    (kept.add if keep_upload else kept.discard)(upload)
                st["keep_uploads"] = sorted(kept)
            if product is not None:
                if it.get("synced_version") is not None:
                    retag.append(it["source"])
                else:
                    it["product"], it["category"] = product, category_of(product)
                    it["product_chosen"] = True
    _update(change)
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

    def download(self, page_id: str, att_id: str) -> bytes:
        return self.get(f"/wiki/rest/api/content/{page_id}/child/attachment/{att_id}/download").content


def _connect() -> tuple["_Api", str, str]:
    s = keystore.get_confluence_settings()
    site, root_kind, root_id = parse_folder_url(s["folder_url"])
    return _Api(site, s["email"], keystore.get_confluence_token()), root_kind, root_id


def _walk(api: _Api, root_kind: str, root_id: str):
    """(page, titles of the folders and pages above it) for every page under
    the root, depth first in Confluence's own order, and the root itself
    when it is a page (its title is read with its body)."""
    if root_kind == "pages":
        yield {"id": root_id, "title": ""}, []
    stack = [(root_kind, root_id, [], None)]
    while stack:
        kind, node, trail, me = stack.pop()
        if me is not None:
            yield me, trail
        below = trail + [me["title"]] if me is not None else trail
        kids = list(api.each(f"/wiki/api/v2/{kind}/{node}/direct-children", limit=250))
        for c in reversed(kids):          # pushed backwards, so they come off in order
            if c.get("type") == "page":
                stack.append(("pages", c["id"], below, c))
            elif c.get("type") == "folder":
                stack.append(("folders", c["id"], below + [c["title"]], None))


class _Text(HTMLParser):
    """Confluence storage format to plain text: blocks on their own lines,
    table cells split by " | ", macro parameters dropped."""
    _BLOCK = {"p", "div", "br", "li", "tr", "table", "pre", "blockquote",
              "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag == "ac:parameter":
            self.skip += 1
        elif tag in self._BLOCK:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag == "ac:parameter":
            self.skip = max(0, self.skip - 1)
        elif tag in self._BLOCK:
            self.out.append("\n")

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
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return f"{title}\n\n{body}" if body else ""


def _body(full: dict) -> str:
    return ((full.get("body") or {}).get("storage") or {}).get("value") or ""


def _safe_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "-", title).strip(" .") or "untitled"


def _link(api: _Api, webui: str | None) -> str | None:
    return f"https://{api.site}/wiki{webui}" if webui else None


# ── check ───────────────────────────────────────────────────────────────

def _check(api: _Api, root_kind: str, root_id: str) -> dict:
    """List the root into the state's items. Indexes nothing."""
    found: dict[str, dict] = {}
    bundles = sorted(_load()["bundles"], key=len, reverse=True)   # innermost first
    combined: dict[str, dict] = {}       # bundle key -> the item being built
    members_of: set[str] = set()         # page items now inside a combined one
    for page, trail in _walk(api, root_kind, root_id):
        pid = page["id"]
        _progress.update(phase="checking", done=len(found))
        full = api.page(pid)
        title = full.get("title") or page["title"]
        here = trail + [title]
        inside = next((b for b in bundles if here[:len(b)] == b), None) if pid != root_id else None
        if inside is not None:
            text = page_text(title, _body(full))
            key = bundle_key(inside)
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
            b["version"] = hashlib.sha1("|".join(b.pop("stamp")).encode()).hexdigest()[:10]
            b["pages"] = len(b["members"])
            found[key] = b

    res = {"at": _now(), "kind": "check", "found": len(found), "new": [], "changed": [], "gone": []}

    def merge(st):
        items = st["items"]
        # The first check finds everything; calling all of it "new" says nothing.
        first_check = not st.get("checked_at")
        taken = {it["source"] for k, it in items.items() if it.get("source") and k in found}
        for key, f in found.items():
            it = items.get(key)
            if it is None:
                cat, prod = _suggest(f["path"] + ([f["title"]] if f["kind"] == "bundle" else []))
                decision = "approved" if st["mode"] == "auto" else "excluded"
                chosen = False
                if f["kind"] == "bundle":
                    pages = [items[f"page:{m[0]}"] for m in f["members"] if f"page:{m[0]}" in items]
                    # Combining approved pages keeps them approved.
                    if any(pg.get("decision") == "approved" for pg in pages):
                        decision = "approved"
                    # ...and pages filed alike keep that filing.
                    filed = {pg.get("product") for pg in pages if pg.get("product_chosen")}
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

def _ingest(content: bytes, source: str, cat: str | None, prod: str | None) -> None:
    from ingest import ingest_file
    ingest_file(content, source, category_key=cat, product_key=prod,
                replace_existing=True)


def _remove(source: str) -> None:
    """What the console's Remove button does, plus the catalogue entry."""
    # Not the /delete_source route: where it asks for a sign-in, a background
    # sync has none, and every replacement failed with "401: Sign-in required".
    from routes_documents import remove_source_now
    remove_source_now(source)
    catalog.refile_source(source, [])


def _bundle_text(api: _Api, it: dict) -> str:
    """A combined folder as one text: its title, then each page under its
    own section heading (the path below the folder), which the chunker keeps
    as a hard boundary and stores as the chunk's section."""
    from chunking import SECTION_CLOSE, SECTION_OPEN
    parts = [it["title"]]
    for n, (pid, heading) in enumerate(it.get("members") or []):
        _progress.update(page=n + 1, pages=len(it["members"]))
        full = api.page(pid)
        text = page_text(full.get("title") or "", _body(full))
        if text:
            parts.append(f"{SECTION_OPEN}{heading}{SECTION_CLOSE}\n{text}")
    _progress.pop("page", None)
    _progress.pop("pages", None)
    return "\n\n".join(parts)


def _apply(api: _Api) -> dict:
    st = _load()
    res = {"at": _now(), "kind": "sync", "added": [], "updated": [], "removed": [],
           "errors": []}
    todo = [(k, dict(it)) for k, it in st["items"].items()
            if status_of({**it, "error": None}) in ("new", "changed", "gone", "remove", "combined")]
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

    matches = _matches(st["items"])
    keep = set(st.get("keep_uploads") or [])
    res["replaced"] = []
    for n, (key, it) in enumerate(todo):
        _progress.update(phase="syncing", done=n, total=len(todo))
        src, what = it["source"], status_of({**it, "error": None})
        try:
            if what in ("gone", "remove", "combined"):
                _remove(src)
                res["removed"].append(src)
                _note(it.get("title") or src, it["kind"], it.get("path") or [], "removed")
                change = {"synced_version": None, "synced_at": None}
            else:
                if it["kind"] == "bundle":
                    content = _bundle_text(api, it).encode("utf-8")
                    version = it.get("version")
                elif it["kind"] == "page":
                    full = api.page(it["id"])
                    content = page_text(full.get("title") or it["title"], _body(full)).encode("utf-8")
                    version = (full.get("version") or {}).get("number")
                else:
                    content = api.download(it["page_id"], it["id"])
                    version = it.get("version")
                uploads = [u for u, whole in matches.get(key, []) if whole and u not in keep]
                cat, prod = _filed(src)
                for u in uploads:
                    if not prod and not it.get("product_chosen"):
                        cat, prod = _filed(u)
                if not prod:
                    cat, prod, _ = _effective(it, _name_index())
                _ingest(content, src, cat, prod or None)
                if prod and not _filed(src)[1]:
                    catalog.attach_source(cat, prod, src)
                for upload in uploads:
                    # Only once the Confluence copy is in, so a failed
                    # download never leaves the manual missing altogether.
                    _remove(upload)
                    res["replaced"].append(f"{upload} -> {src}")
                    _note(it.get("title") or src, it["kind"], it.get("path") or [], "replaced")
                res["added" if what == "new" else "updated"].append(src)
                _note(it.get("title") or src, it["kind"], it.get("path") or [],
                      "added" if what == "new" else "updated")
                change = {"synced_version": version, "synced_at": _now(), "version": version}
            change["error"] = None
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
    done = {k for k, _ in todo}
    for key, it in st["items"].items():
        if key in done or status_of(it) != "synced":
            continue
        for upload, whole in matches.get(key, []):
            if not whole or upload in keep:
                continue
            try:
                _remove(upload)
                res["replaced"].append(f"{upload} -> {it.get('source')}")
                _note(it.get("title") or upload, it["kind"], it.get("path") or [], "replaced")
            except Exception as exc:
                res["errors"].append(f"{upload}: not removed: {exc}")
    return res


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
        _progress.update(phase="connecting")
        try:
            api, root_kind, root_id = _connect()
            res = _check(api, root_kind, root_id) if check else {}
            if apply:
                res = {**_apply(api), **{k: res[k] for k in ("found", "new", "changed", "gone") if k in res},
                       "checked": check}
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


def _loop() -> None:
    from app_state import capability
    time.sleep(300)   # after startup and the model warmup
    while True:
        interval = _interval_seconds()
        if interval <= 0:
            return
        # Counted from the last check, not from startup: restarting the
        # server must not re-read the whole folder every time.
        wait = interval - _since_last_check()
        if wait > 60:
            time.sleep(wait)
            continue
        if configured() and capability("search") and not running():
            run_in_background(apply=True, check=True)
        time.sleep(interval)


def _since_last_check() -> float:
    at = _load().get("checked_at")
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(at)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def start() -> None:
    if _interval_seconds() <= 0:
        logger.info("periodic Confluence sync disabled (CONFLUENCE_SYNC_HOURS=0)")
        return
    threading.Thread(target=_loop, name="confluence-sync-timer", daemon=True).start()
