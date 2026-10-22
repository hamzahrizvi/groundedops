"""Manuals from one Confluence folder or page (the "Website" one), with a
review step between Confluence and the bot.

Two steps, kept apart so a person can stand between them:

  check  -- list what is under the root: every page with real text and every
            PDF, Word or text file attached to those pages. Records each as
            an item with its Confluence version. Indexes nothing.
  apply  -- bring the index in line with the approved items: add new ones,
            re-index changed ones, remove ones excluded or gone from
            Confluence. Only documents this sync added are ever removed.

"Sync now" and the daily run do both. Each item carries a decision:
approved, excluded, or pending. In "auto" mode a newly found document is
approved at once (sync everything); in "review" mode it waits as pending
until someone approves it on the console's Confluence page. A document that
was approved stays approved when it changes in Confluence.

Filing: a document already filed in the catalogue keeps that filing. A new
one is suggested the product its nearest folder or page is named after,
and the suggestion can be changed before or after it is synced.

Access is a scoped Atlassian API token (read-only granular scopes) for an
account that can see only what customers may see. Scoped tokens work only
through api.atlassian.com, addressed by the site's cloud id.
"""
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
DECISIONS = ("approved", "excluded", "pending")

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
        return "gone" if synced else "missing"
    if it.get("decision") == "excluded":
        return "remove" if synced else "excluded"
    if it.get("decision") == "pending":
        return "waiting"
    if not synced:
        return "new"
    return "synced" if it.get("synced_version") == it.get("version") else "changed"


def _filed(source: str) -> tuple[str | None, str]:
    """(category key, "k1,k2") a source is filed under in the catalogue."""
    data = catalog.load_catalog()
    held = [(c["key"], p["key"]) for c in data["categories"]
            for p in c.get("products", []) if source in p.get("sources", [])]
    return (held[0][0] if held else None), ",".join(k for _, k in held)


def _suggest(path: list[str]) -> tuple[str | None, str]:
    """The product the nearest folder or page above is named after."""
    by_name: dict[str, list] = {}
    for c in catalog.load_catalog()["categories"]:
        for p in c.get("products", []):
            by_name.setdefault(p["name"].strip().lower(), []).append((c["key"], p["key"]))
    for title in reversed(path):
        hit = by_name.get(title.strip().lower())
        if hit and len(hit) == 1:           # a name two categories share files nothing
            return hit[0]
    return None, ""


def category_of(product_keys: str) -> str | None:
    first = next((k for k in (product_keys or "").split(",") if k.strip()), "").strip()
    for c in catalog.load_catalog()["categories"]:
        if any(p["key"] == first for p in c.get("products", [])):
            return c["key"]
    return None


def overview() -> dict:
    """Everything the console's Confluence page shows."""
    st = _load()
    filed: dict[str, str] = {}             # source -> "k1,k2", one catalogue read
    for c in catalog.load_catalog()["categories"]:
        for p in c.get("products", []):
            for src in p.get("sources", []):
                filed[src] = (filed[src] + "," if src in filed else "") + p["key"]
    items = []
    for key, it in st["items"].items():
        row = {k: it.get(k) for k in ("kind", "title", "path", "url", "size", "version",
                                      "decision", "source", "synced_at", "error", "present")}
        row["key"] = key
        row["status"] = status_of(it)
        if it.get("synced_version") is not None:
            row["product"] = filed.get(it["source"], "")   # the catalogue is the truth once synced
        else:
            row["product"] = it.get("product") or ""
        items.append(row)
    items.sort(key=lambda r: ([p.lower() for p in r["path"] or []], (r["title"] or "").lower()))
    return {"mode": st["mode"], "checked_at": st.get("checked_at"),
            "items": items, "history": st["history"][::-1],
            "running": running(), "configured": configured()}


# ── decisions from the console ──────────────────────────────────────────

def set_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError("mode must be review or auto")
    _update(lambda st: st.__setitem__("mode", mode))


def set_items(keys: list[str], decision: str | None = None,
              product: str | None = None) -> None:
    """Approve, exclude or re-file items. Decisions take effect at the next
    sync; a new product for a document already synced takes effect now."""
    if decision is not None and decision not in DECISIONS:
        raise ValueError("decision must be approved, excluded or pending")
    retag = []

    def change(st):
        for k in keys:
            it = st["items"].get(k)
            if not it:
                continue
            if decision:
                it["decision"] = decision
                it.pop("error", None)
            if product is not None:
                if it.get("synced_version") is not None:
                    retag.append(it["source"])
                else:
                    it["product"], it["category"] = product, category_of(product)
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
        r = self.http.get(self.root + path, params=params, timeout=120)
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
    the root, and the root itself when it is a page (its title is read with
    its body)."""
    if root_kind == "pages":
        yield {"id": root_id, "title": ""}, []
    todo = [(root_kind, root_id, [])]
    while todo:
        kind, node, trail = todo.pop()
        for c in api.each(f"/wiki/api/v2/{kind}/{node}/direct-children", limit=250):
            if c.get("type") == "page":
                yield c, trail
                todo.append(("pages", c["id"], trail + [c["title"]]))
            elif c.get("type") == "folder":
                todo.append(("folders", c["id"], trail + [c["title"]]))


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
    for page, trail in _walk(api, root_kind, root_id):
        pid = page["id"]
        _progress.update(phase="checking", done=len(found))
        full = api.page(pid)
        title = full.get("title") or page["title"]
        # The root page's own text is the container's intro, not a manual;
        # files attached to it still count.
        if pid != root_id and len(page_text(title, _body(full))) >= _MIN_PAGE_TEXT:
            found[f"page:{pid}"] = {
                "kind": "page", "id": pid, "title": title, "path": trail,
                "name": _safe_name(title) + ".txt",
                "version": (full.get("version") or {}).get("number"),
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
                "size": att.get("fileSize"),
                "url": _link(api, att.get("webuiLink"))}
            _note(att.get("title") or name, "file", trail + ([title] if title else []), "found")

    res = {"at": _now(), "kind": "check", "found": len(found), "new": [], "changed": [], "gone": []}

    def merge(st):
        items = st["items"]
        taken = {it["source"] for k, it in items.items() if it.get("source") and k in found}
        for key, f in found.items():
            it = items.get(key)
            if it is None:
                cat, prod = _suggest(f["path"])
                it = items[key] = {"decision": "approved" if st["mode"] == "auto" else "pending",
                                   "category": cat, "product": prod, "synced_version": None}
                res["new"].append(f["title"])
            elif it.get("synced_version") is not None and it["synced_version"] != f["version"]:
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
    _update(merge)
    return res


# ── apply ───────────────────────────────────────────────────────────────

def _ingest(content: bytes, source: str, cat: str | None, prod: str | None) -> None:
    from ingest import ingest_file
    ingest_file(content, source, category_key=cat, product_key=prod,
                replace_existing=True)


def _remove(source: str) -> None:
    """What the console's Remove button does, plus the catalogue entry."""
    from routes_documents import DeleteSourceRequest, remove_source
    remove_source(DeleteSourceRequest(source=source))
    catalog.refile_source(source, [])


def _apply(api: _Api) -> dict:
    st = _load()
    res = {"at": _now(), "kind": "sync", "added": [], "updated": [], "removed": [],
           "waiting": 0, "errors": []}
    todo = [(k, dict(it)) for k, it in st["items"].items()
            if status_of({**it, "error": None}) in ("new", "changed", "gone", "remove")]
    res["waiting"] = sum(1 for it in st["items"].values() if status_of(it) == "waiting")
    if st.get("found_nothing") and st["items"]:
        # Nothing listed at all is a permissions change or an emptied folder
        # far more often than a real removal of every manual.
        res["errors"].append("Confluence listed nothing, so nothing was removed. "
                             "Check the account can still see the folder.")
        todo = [(k, it) for k, it in todo if it.get("present", True)]

    for n, (key, it) in enumerate(todo):
        _progress.update(phase="syncing", done=n, total=len(todo))
        src, what = it["source"], status_of({**it, "error": None})
        try:
            if what in ("gone", "remove"):
                _remove(src)
                res["removed"].append(src)
                _note(it.get("title") or src, it["kind"], it.get("path") or [], "removed")
                change = {"synced_version": None, "synced_at": None}
            else:
                if it["kind"] == "page":
                    full = api.page(it["id"])
                    content = page_text(full.get("title") or it["title"], _body(full)).encode("utf-8")
                    version = (full.get("version") or {}).get("number")
                else:
                    content = api.download(it["page_id"], it["id"])
                    version = it.get("version")
                cat, prod = _filed(src)
                if not prod:
                    prod = it.get("product") or ""
                    cat = it.get("category") or category_of(prod)
                _ingest(content, src, cat, prod or None)
                if prod and not _filed(src)[1]:
                    catalog.attach_source(cat, prod, src)
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
    return res


# ── runs ────────────────────────────────────────────────────────────────

class Busy(RuntimeError):
    pass


def run(apply: bool = True) -> dict:
    """Check, then (unless apply=False) sync. Returns and records the result."""
    if not _run_lock.acquire(blocking=False):
        raise Busy("A Confluence check or sync is already running")
    try:
        _progress.clear()
        _progress.update(phase="connecting")
        try:
            api, root_kind, root_id = _connect()
            res = _check(api, root_kind, root_id)
            if apply:
                res = {**_apply(api), "found": res["found"], "new": res["new"],
                       "changed": res["changed"], "gone": res["gone"]}
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


def run_in_background(apply: bool = True) -> None:
    def go():
        try:
            run(apply)
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
        if configured() and capability("search") and not running():
            run_in_background(apply=True)
        time.sleep(interval)


def start() -> None:
    if _interval_seconds() <= 0:
        logger.info("periodic Confluence sync disabled (CONFLUENCE_SYNC_HOURS=0)")
        return
    threading.Thread(target=_loop, name="confluence-sync-timer", daemon=True).start()
