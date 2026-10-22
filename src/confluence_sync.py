"""Keep the manuals in one Confluence folder (the "Website" folder) indexed.

Every page under the folder, at any depth, is indexed as text, and every PDF,
Word or text file attached to those pages as the file itself, through the
same ingest_file an upload goes through.

Filing: a document already filed in the catalogue keeps that filing. A new
one is filed under a product when a folder or page above it carries that
product's name ("Website / Smart Hopper 3 / Installation"); otherwise it
lands unassigned and is filed in the console like any upload.

A document that leaves the folder is removed from the index, but only one
this sync added: the state file lists them. Uploads made by hand are never
touched, unless Confluence holds a document of exactly the same name, which
it then keeps up to date.

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
# A page with less text than this is a wrapper round its attachment, not a
# manual, and indexing it would only add a near-empty source.
_MIN_PAGE_TEXT = 200
# A folder, or a page whose child pages hold the manuals: both are a
# "Website" container in Confluence, depending on how the space was built.
_FOLDER_URL = re.compile(
    r"^https://([a-z0-9-]+\.atlassian\.net)/wiki/spaces/[^/]+/(folder|pages)/(\d+)", re.I)

_lock = threading.Lock()


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


def running() -> bool:
    return _lock.locked()


def last_result() -> dict | None:
    return jsonstore.load(_STATE, {}, label="Confluence sync").get("last")


# ── Confluence ──────────────────────────────────────────────────────────

class _Api:
    def __init__(self, site: str, email: str, token: str):
        r = requests.get(f"https://{site}/_edge/tenant_info", timeout=20)
        r.raise_for_status()
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


# ── filing ──────────────────────────────────────────────────────────────

def _filing(source: str, trail: list[str]) -> tuple[str | None, str | None, bool]:
    """(category key, product keys, already filed) for a document."""
    data = catalog.load_catalog()
    held = [(c["key"], p["key"]) for c in data["categories"]
            for p in c.get("products", []) if source in p.get("sources", [])]
    if held:
        return held[0][0], ",".join(k for _, k in held), True
    by_name: dict[str, list] = {}
    for c in data["categories"]:
        for p in c.get("products", []):
            by_name.setdefault(p["name"].strip().lower(), []).append((c["key"], p["key"]))
    for title in reversed(trail):           # nearest folder or page wins
        hit = by_name.get(title.strip().lower())
        if hit and len(hit) == 1:           # a name two categories share files nothing
            return hit[0][0], hit[0][1], False
    return None, None, False


def _safe_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "-", title).strip(" .") or "untitled"


# ── the sync ────────────────────────────────────────────────────────────

class Busy(RuntimeError):
    pass


def sync() -> dict:
    """One full pass. Returns (and records) what changed."""
    if not _lock.acquire(blocking=False):
        raise Busy("A Confluence sync is already running")
    try:
        return _sync()
    finally:
        _lock.release()


def _ingest(content: bytes, source: str, cat: str | None, prod: str | None) -> None:
    from ingest import ingest_file
    ingest_file(content, source, category_key=cat, product_key=prod,
                replace_existing=True)


def _remove(source: str) -> None:
    """What the console's Remove button does, plus the catalogue entry."""
    from routes_documents import DeleteSourceRequest, remove_source
    remove_source(DeleteSourceRequest(source=source))
    catalog.refile_source(source, [])


def _sync() -> dict:
    s = keystore.get_confluence_settings()
    site, root_kind, root_id = parse_folder_url(s["folder_url"])
    api = _Api(site, s["email"], keystore.get_confluence_token())

    state = jsonstore.load(_STATE, {}, label="Confluence sync")
    held: dict = state.get("sources", {})       # source -> "page:<id>:<version>"
    seen: dict = {}
    res = {"added": [], "updated": [], "unchanged": 0, "removed": [],
           "unfiled": [], "errors": []}

    def take(source: str, tag: str, trail: list[str], fetch) -> None:
        if source in seen:                      # two pages of the same title
            source = f"{os.path.splitext(source)[0]} ({tag.split(':')[1]}){os.path.splitext(source)[1]}"
        if source in held:
            seen[source] = held[source]         # kept as it was if this one fails
        if held.get(source) == tag:
            res["unchanged"] += 1
            return
        try:
            content = fetch()
            if not content:
                return
            cat, prod, filed = _filing(source, trail)
            _ingest(content, source, cat, prod)
            if cat and prod and not filed:
                catalog.attach_source(cat, prod, source)
            if not prod:
                res["unfiled"].append(source)
            res["updated" if source in held else "added"].append(source)
            seen[source] = tag
        except Exception as exc:
            logger.warning("Confluence sync: %s failed: %s", source, exc)
            res["errors"].append(f"{source}: {exc}")

    for page, trail in _walk(api, root_kind, root_id):
        pid = page["id"]
        full = api.get(f"/wiki/api/v2/pages/{pid}", **{"body-format": "storage"}).json()
        title = full.get("title") or page["title"]
        ver = (full.get("version") or {}).get("number")
        text = page_text(title, ((full.get("body") or {}).get("storage") or {}).get("value"))
        # The Website page's own text is the container's intro, not a manual;
        # files attached to it still count.
        if len(text) >= _MIN_PAGE_TEXT and pid != root_id:
            take(_safe_name(title) + ".txt", f"page:{pid}:{ver}", trail,
                 lambda t=text: t.encode("utf-8"))
        for att in api.each(f"/wiki/api/v2/pages/{pid}/attachments", limit=250):
            name = _safe_name(att.get("title") or "")
            if not name.lower().endswith(_FILE_TYPES):
                continue
            aid = att["id"]
            take(name, f"att:{aid}:{(att.get('version') or {}).get('number')}",
                 trail + [title],
                 lambda p=pid, a=aid: api.get(
                     f"/wiki/rest/api/content/{p}/child/attachment/{a}/download").content)

    gone = [src for src in held if src not in seen]
    if gone and not seen:
        # Nothing listed at all is a permissions change or an emptied folder
        # far more often than a real removal of every manual.
        res["errors"].append("The folder listed nothing, so nothing was removed. "
                             "Check the account can still see it.")
        seen = dict(held)
    else:
        for src in gone:
            try:
                _remove(src)
                res["removed"].append(src)
            except Exception as exc:
                res["errors"].append(f"{src}: not removed: {exc}")
                seen[src] = held[src]

    res["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    jsonstore.save(_STATE, {"sources": seen, "last": res}, label="Confluence sync")
    logger.info("Confluence sync: %d added, %d updated, %d unchanged, %d removed, %d errors",
                len(res["added"]), len(res["updated"]), res["unchanged"],
                len(res["removed"]), len(res["errors"]))
    return res


def sync_in_background() -> None:
    def run():
        try:
            sync()
        except Busy:
            pass                     # a second click while one runs
        except Exception as exc:
            logger.warning("Confluence sync failed: %s", exc)
            jsonstore.save(_STATE, {**jsonstore.load(_STATE, {}, label="Confluence sync"),
                                    "last": {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                             "failed": str(exc)}}, label="Confluence sync")
    threading.Thread(target=run, name="confluence-sync", daemon=True).start()


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
            sync_in_background()
        time.sleep(interval)


def start() -> None:
    if _interval_seconds() <= 0:
        logger.info("periodic Confluence sync disabled (CONFLUENCE_SYNC_HOURS=0)")
        return
    threading.Thread(target=_loop, name="confluence-sync-timer", daemon=True).start()
