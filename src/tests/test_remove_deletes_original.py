"""Remove deletes the retained original too (D-20261024-18): no download, no
listing, no rebuild bringing it back, and an internal mark outlives a failed
delete."""
import os
import sys
import tempfile
import types
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMP = tempfile.mkdtemp()
os.environ["INTERNAL_SOURCES_PATH"] = os.path.join(_TMP, "internal_sources.json")
os.environ["CHROMA_DIR"] = os.path.join(_TMP, "chroma_db")
os.environ["SOURCE_FILE_DIR"] = os.path.join(_TMP, "documents")
for _k, _n in (("CONFLUENCE_SYNC_STATE", "cs.json"), ("CATALOG_CONFIG", "cat.json"),
               ("ACCOUNTS_PATH", "acc.json"), ("ACCOUNT_REQUESTS_PATH", "req.json"),
               ("AUTH_TOKENS_PATH", "tok.json"), ("ENV_FILE_PATH", "test.env")):
    os.environ[_k] = os.path.join(_TMP, _n)

import db  # noqa: E402
import docindex  # noqa: E402
import docstore  # noqa: E402
import ingest  # noqa: E402
import reindex  # noqa: E402
import retrieval_db as rdb  # noqa: E402


class _Vector:
    def tolist(self):
        return [0.1, 0.2]


class _Collection:
    def __init__(self):
        self.rows = {}

    def get(self, where=None, include=None, **kw):
        rows = [(k, v) for k, v in self.rows.items()
                if not where or v[1].get("source") == where.get("source")]
        return {"ids": [k for k, _ in rows], "documents": [v[0] for _, v in rows],
                "metadatas": [dict(v[1]) for _, v in rows]}

    def add(self, ids, documents, embeddings, metadatas):
        for k, t, m in zip(ids, documents, metadatas):
            self.rows[k] = (t, m)

    def delete(self, ids):
        for k in ids:
            self.rows.pop(k, None)

    def count(self):
        return len(self.rows)

    def chunks(self, source):
        return [v for v in self.rows.values() if v[1].get("source") == source]


COL = _Collection()
for p in (patch.object(db, "get_collection", return_value=COL),
          patch.object(ingest, "get_collection", return_value=COL),
          patch.object(ingest, "extract_pages_report",
                       return_value=([(1, "Setting up the device takes four steps.")],
                                     {"pages": 1})),
          patch.object(ingest, "embed_texts", side_effect=lambda t: [_Vector() for _ in t]),
          patch.object(ingest, "_dedupe_enabled", return_value=False),
          patch.object(reindex, "catalog_assignment",
                       return_value={"Pricing.txt": ("cat", "prod_a")})):
    p.start()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import confluence_sync  # noqa: E402
import routes_documents  # noqa: E402
routes_documents._require_admin = lambda *a, **k: None
_app = FastAPI()
_app.include_router(routes_documents.router)
client = TestClient(_app)            # no gate middleware: a private-address caller
patch.object(confluence_sync.catalog, "refile_source", lambda *a, **k: None).start()


def _reset():
    COL.rows.clear()
    docindex.invalidate()
    for n in list(rdb.internal_sources()):
        rdb.set_internal([n], False, reindex=False)
    d = docstore.store_dir()
    if os.path.isdir(d):
        for e in os.listdir(d):
            if os.path.isfile(os.path.join(d, e)):
                os.remove(os.path.join(d, e))


def originals():
    d = docstore.store_dir()
    return sorted(e for e in os.listdir(d)
                  if os.path.isfile(os.path.join(d, e)) and e != docstore.MANIFEST_NAME)


def _rows(name):
    return [r for r in client.get("/admin/sources").json()["sources"] if r["source"] == name]


# The upload route starts a thread; run its worker inline instead.
_INLINE = types.SimpleNamespace(
    Thread=lambda target, args, daemon=True: types.SimpleNamespace(start=lambda: target(*args)))


def _upload(name, content, replace):
    with patch.object(routes_documents, "capability", return_value=True),          patch.object(routes_documents, "threading", _INLINE):
        return client.post("/upload", files={"file": (name, content)},
                           headers={"replace-source": replace})


def test_a_console_remove_deletes_the_original():
    _reset()
    ingest.ingest_file(b"manual", "Manual.txt")
    assert client.get("/source_file/Manual.txt").status_code == 200
    assert client.post("/delete_source", json={"source": "Manual.txt"}).status_code == 200
    assert docstore.find("Manual.txt") is None
    assert "Manual.txt" not in docstore.load_manifest()["documents"]
    assert client.get("/source_file/Manual.txt").status_code == 404
    assert COL.chunks("Manual.txt") == []


def test_b_sync_remove_of_an_internal_document():
    _reset()
    ingest.ingest_file(b"price list", "Pricing.txt")
    rdb.set_internal(["Pricing.txt"], True)
    pre = _rows("Pricing.txt")
    assert pre and pre[0]["internal"] is True
    confluence_sync._remove("Pricing.txt")
    assert not rdb.is_marked_internal("Pricing.txt")
    assert docstore.find("Pricing.txt") is None
    assert client.get("/source_file/Pricing.txt").status_code == 404
    assert _rows("Pricing.txt") == []
    assert COL.chunks("Pricing.txt") == []


def test_c_a_replaced_page_ends_with_one_original():
    _reset()
    confluence_sync._ingest(b"v1", "Page.txt", None, None)
    confluence_sync._ingest(b"v2", "Page.txt", None, None)
    assert originals() == ["Page.txt"]
    assert open(docstore.find("Page.txt"), "rb").read() == b"v2"
    assert client.get("/source_file/Page.txt").status_code == 200
    confluence_sync._remove("Page.txt")
    confluence_sync._ingest(b"bundle", "Page.txt", None, None)
    assert originals() == ["Page.txt"]
    assert open(docstore.find("Page.txt"), "rb").read() == b"bundle"
    ingest.ingest_file(b"old", "Old Manual.txt")
    confluence_sync._remove("Old Manual.txt")
    assert originals() == ["Page.txt"]
    rdb.set_internal(["Page.txt"], True)
    assert client.get("/source_file/Page.txt").status_code == 404


def test_d_upload_under_a_new_name_deletes_the_old_original():
    _reset()
    ingest.ingest_file(b"old", "Old.txt")
    routes_documents._INGEST_JOBS["j"] = {}
    routes_documents._ingest_worker("j", b"new", "New.txt", {}, replace_source="Old.txt")
    assert docstore.find("Old.txt") is None
    assert docstore.find("New.txt")
    assert COL.chunks("Old.txt") == []


def test_e_a_failed_delete_keeps_the_mark():
    _reset()
    ingest.ingest_file(b"secret", "Secret.txt")
    rdb.set_internal(["Secret.txt"], True)
    with patch.object(docstore, "delete", side_effect=OSError("locked")):
        try:
            routes_documents.remove_source_now("Secret.txt")
            raise AssertionError("expected OSError")
        except OSError:
            pass
    assert rdb.is_marked_internal("Secret.txt")
    assert docstore.find("Secret.txt")


def test_f_delete_reaches_the_legacy_folder_and_spares_the_manifest():
    _reset()
    legacy = os.path.join(_TMP, "legacy")
    os.makedirs(legacy, exist_ok=True)
    with open(os.path.join(legacy, "Legacy.txt"), "wb") as fh:
        fh.write(b"old")
    with patch.object(docstore, "LEGACY_DIR", legacy):
        assert docstore.find("Legacy.txt")
        assert docstore.delete("Legacy.txt") is True
        assert docstore.find("Legacy.txt") is None
    ingest.ingest_file(b"any", "Any.txt")
    assert docstore.delete("manifest.json") is False
    assert os.path.isfile(os.path.join(docstore.store_dir(), docstore.MANIFEST_NAME))
    assert docstore.delete("Nothing.txt") is False


def test_g_a_new_version_differing_only_in_case_keeps_one_document():
    _reset()
    ingest.ingest_file(b"old", "Manual.txt")
    r = _upload("manual.txt", b"new", "Manual.txt")
    assert r.status_code == 200, r.text
    assert originals() == ["Manual.txt"]
    assert open(docstore.find("Manual.txt"), "rb").read() == b"new"
    assert {m.get("source") for _, m in COL.rows.values()} == {"Manual.txt"}
    assert list(docstore.load_manifest()["documents"]) == ["Manual.txt"]
    assert client.get("/source_file/Manual.txt").status_code == 200


def test_h_a_new_version_under_another_name_still_removes_the_old():
    _reset()
    ingest.ingest_file(b"old", "Manual.txt")
    r = _upload("Guide.txt", b"new", "Manual.txt")
    assert r.status_code == 200, r.text
    assert originals() == ["Guide.txt"]
    assert {m.get("source") for _, m in COL.rows.values()} == {"Guide.txt"}
    assert docstore.find("Manual.txt") is None


def test_i_the_store_list_is_never_served_as_a_document():
    # D-20261025-03: the manifest names every kept document, internal ones too.
    _reset()
    ingest.ingest_file(b"manual", "Manual.txt")
    d = docstore.store_dir()
    for extra in ("manifest.json.tmp", "manifest.json.corrupt"):
        with open(os.path.join(d, extra), "wb") as fh:
            fh.write(b"{}")
    assert os.path.isfile(os.path.join(d, docstore.MANIFEST_NAME))
    for name in ("manifest.json", "MANIFEST.JSON", "manifest.json.tmp", "manifest.json.corrupt", "..manifest.json"):
        assert docstore.find(name) is None, name
        assert client.get(f"/source_file/{name}").status_code == 404, name
    assert client.get("/source_file/Manual.txt").status_code == 200


class _Page:
    """A Confluence root page with one child page "manual" (manual.txt)."""
    def each(self, path, **params):
        parts = path.split("/")
        if parts[-1] == "direct-children" and parts[-2] == "1":
            return iter([{"type": "page", "id": "20", "title": "manual"}])
        return iter([])

    def page(self, pid):
        return {"title": {"1": "Root", "20": "manual"}[pid], "version": {"number": 1},
                "body": {"storage": {"value": "<p>" + "Fit the hopper to the rail. " * 20 + "</p>"}}}


def _case_insensitive_disk():
    d = tempfile.mkdtemp(dir=_TMP)
    open(os.path.join(d, "a"), "w").close()
    return os.path.exists(os.path.join(d, "A"))


class _Attached(_Page):
    """A Confluence root page with "manual.pdf" attached."""
    def each(self, path, **params):
        if path.endswith("/pages/1/attachments"):
            return iter([{"id": "att1", "title": "manual.pdf", "version": {"number": 1}}])
        return iter([])

    def attachment(self, att_id):
        return {"id": att_id, "version": {"number": 1}}

    def download(self, page_id, att_id):
        return b"%PDF from Confluence"


def _cut_one(tmp_path, source, progress=None):
    """figures.extract_figures for a test: one crop in the document's folder."""
    import figures
    os.makedirs(figures.figures_dir(source), exist_ok=True)
    open(os.path.join(figures.figures_dir(source), "p1_1.png"), "wb").close()
    return {}


def _fresh_sync():
    _reset()
    if os.path.exists(os.environ["CONFLUENCE_SYNC_STATE"]):
        os.remove(os.environ["CONFLUENCE_SYNC_STATE"])


def _sync(api=None):
    with patch.object(confluence_sync, "_connect", return_value=(api or _Page(), "pages", "1")):
        if not confluence_sync.overview()["items"]:
            confluence_sync.run(apply=False, check=True)
        keys = [i["key"] for i in confluence_sync.overview()["items"]]
        confluence_sync.set_items(keys, decision="approved")
        return confluence_sync.run(apply=True, check=True)


def _one_document(name):
    if not _case_insensitive_disk():
        print("SKIP case-insensitive checks: this disk tells Manual.txt and manual.txt apart")
        return
    assert [o.casefold() for o in originals()] == [name.casefold()], originals()
    assert {m.get("source") for _, m in COL.rows.values()} == {name}, COL.rows
    assert list(docstore.load_manifest()["documents"]) == [name]
    assert client.get("/source_file/" + name).status_code == 200


def test_j_a_synced_page_replacing_its_case_twin_upload_keeps_one_document():
    _fresh_sync()
    ingest.ingest_file(b"old", "Manual.txt")
    res = _sync()
    assert res.get("replaced") == ["Manual.txt -> manual.txt"], res
    _one_document("manual.txt")

    # Uploaded again by hand as "Manual.txt": the next sync removes the
    # upload and the synced copy keeps its original.
    ingest.ingest_file(b"again", "Manual.txt")
    res = _sync()
    assert res.get("replaced") == ["Manual.txt -> manual.txt"], res
    _one_document("manual.txt")
    # the Confluence text, not the upload that overwrote it on Windows
    assert b"Fit the hopper" in open(docstore.find("manual.txt"), "rb").read()


def test_k_an_upload_under_another_name_is_still_replaced():
    _fresh_sync()
    ingest.ingest_file(b"old", "Manual-v2.txt")
    res = _sync()
    assert res.get("replaced") == ["Manual-v2.txt -> manual.txt"], res
    assert docstore.find("Manual-v2.txt") is None
    assert {m.get("source") for _, m in COL.rows.values()} == {"manual.txt"}


def test_l_a_synced_pdf_keeps_its_figures_when_its_case_twin_goes():
    import figures
    _fresh_sync()
    with patch.object(figures, "extract_figures", _cut_one):
        ingest.ingest_file(b"%PDF uploaded", "Manual.pdf")
        res = _sync(_Attached())
    assert res.get("replaced") == ["Manual.pdf -> manual.pdf"], res
    # Removed after the ingest, the upload took the copy's figures folder
    # with it on Windows (one folder for both spellings).
    assert os.listdir(figures.figures_dir("manual.pdf")) == ["p1_1.png"]
    _one_document("manual.pdf")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
