"""Confluence manuals sync against a fake Confluence.

Nothing reaches Atlassian, the index or the real catalogue: the API client,
ingest, removal and the catalogue are swapped for fakes, and the state file
is a temp file.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import confluence_sync as cs  # noqa: E402

BODY = "<p>" + "Fit the hopper to the rail and tighten both screws. " * 6 + "</p>"


class FakeApi:
    """Website / Smart Hopper 3 / Installation (page, with a PDF)
       Website / Overview (page)"""
    tree = {
        ("folders", "1"): [{"type": "folder", "id": "10", "title": "Smart Hopper 3"},
                           {"type": "page", "id": "30", "title": "Overview"}],
        ("folders", "10"): [{"type": "page", "id": "20", "title": "Installation"}],
    }
    attachments = {"20": [{"id": "att5", "title": "SH3 manual.pdf", "version": {"number": 2}},
                          {"id": "att6", "title": "photo.png", "version": {"number": 1}}]}

    def __init__(self, *a):
        pass

    def each(self, path, **params):
        parts = path.split("/")
        if parts[-1] == "direct-children":
            return iter(self.tree.get((parts[-3], parts[-2]), []))
        return iter(self.attachments.get(parts[-2], []))

    def get(self, path, **params):
        class R:
            pass
        r = R()
        if "/download" in path:
            r.content = b"%PDF fake"
        else:
            pid = path.rsplit("/", 1)[-1]
            r.json = lambda: {"version": {"number": 1}, "title": {"20": "Installation", "30": "Overview"}.get(pid, "Website"),
                              "body": {"storage": {"value": BODY if pid != "30" else "<p>Hi</p>"}}}
        return r


class FakeCatalog:
    def __init__(self):
        self.data = {"categories": [{"key": "coin", "name": "Coin Hoppers", "products": [
            {"key": "sh3", "name": "Smart Hopper 3", "sources": []}]}]}

    def load_catalog(self):
        return self.data

    def attach_source(self, cat, prod, source):
        self.data["categories"][0]["products"][0]["sources"].append(source)


def _run(api_cls, state_path, url="https://itl.atlassian.net/wiki/spaces/WEB/folder/1"):
    ingested, removed = [], []
    saved = (cs._Api, cs._ingest, cs._remove, cs.catalog, cs._STATE,
             cs.keystore.get_confluence_settings, cs.keystore.get_confluence_token)
    cs._Api = api_cls
    cs._ingest = lambda content, source, cat, prod: ingested.append((source, prod))
    cs._remove = removed.append
    cs.catalog = FakeCatalog()
    cs._STATE = state_path
    cs.keystore.get_confluence_settings = lambda: {
        "folder_url": url,
        "email": "sync@example.com", "token_set": True}
    cs.keystore.get_confluence_token = lambda: "t"
    try:
        return cs.sync(), ingested, removed
    finally:
        (cs._Api, cs._ingest, cs._remove, cs.catalog, cs._STATE,
         cs.keystore.get_confluence_settings, cs.keystore.get_confluence_token) = saved


def test_folder_url():
    assert cs.parse_folder_url(
        "https://itl.atlassian.net/wiki/spaces/WEB/folder/123?atlOrigin=x") == ("itl.atlassian.net", "folders", "123")
    assert cs.parse_folder_url(
        "https://itl.atlassian.net/wiki/spaces/CS/pages/1069318147/Website") == ("itl.atlassian.net", "pages", "1069318147")
    assert cs.normalise_folder_url(
        "https://itl.atlassian.net/wiki/spaces/CS/pages/1069318147/Website").endswith("/pages/1069318147")
    assert cs.normalise_folder_url(
        "https://itl.atlassian.net/wiki/spaces/WEB/folder/123?atlOrigin=x").endswith("/folder/123")
    for bad in ("https://evil.example/wiki/spaces/WEB/folder/1",
                "http://itl.atlassian.net/wiki/spaces/WEB/folder/1", ""):
        try:
            cs.parse_folder_url(bad)
            assert False, bad
        except ValueError:
            pass


def test_page_text():
    t = cs.page_text("Install", '<h1>Steps</h1><p>One &amp; two</p>'
                     '<ac:structured-macro><ac:parameter ac:name="x">info</ac:parameter>'
                     '<ac:rich-text-body><p>Note</p></ac:rich-text-body></ac:structured-macro>'
                     '<table><tr><td>A</td><td>B</td></tr></table>')
    assert t.startswith("Install\n\nSteps") and "One & two" in t and "info" not in t
    assert "Note" in t and "| A | B" in t


def test_sync_files_and_removes_only_its_own():
    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "s.json")
        res, ingested, removed = _run(FakeApi, state)
        # the page body and its PDF, both under the product its folder names;
        # the near-empty Overview page and the PNG are skipped
        assert sorted(ingested) == [("Installation.txt", "sh3"), ("SH3 manual.pdf", "sh3")], ingested
        assert sorted(res["added"]) == ["Installation.txt", "SH3 manual.pdf"] and not res["errors"]

        # second run: nothing changed, nothing fetched again
        res, ingested, removed = _run(FakeApi, state)
        assert ingested == [] and res["unchanged"] == 2 and removed == []

        # the PDF leaves the folder: only it is removed
        class Gone(FakeApi):
            attachments = {}
        res, ingested, removed = _run(Gone, state)
        assert removed == ["SH3 manual.pdf"], removed

        # the folder lists nothing (lost access): nothing is removed
        class Empty(FakeApi):
            tree = {}
        res, ingested, removed = _run(Empty, state)
        assert removed == [] and res["errors"], res


def test_sync_from_a_page():
    """The manuals under a Website PAGE rather than a folder."""
    class PageRoot(FakeApi):
        tree = {("pages", "1"): FakeApi.tree[("folders", "1")],
                ("folders", "10"): FakeApi.tree[("folders", "10")]}
    with tempfile.TemporaryDirectory() as d:
        res, ingested, removed = _run(PageRoot, os.path.join(d, "s.json"),
                                      "https://itl.atlassian.net/wiki/spaces/CS/pages/1")
        # the Website page itself is a short index page and is skipped
        assert sorted(ingested) == [("Installation.txt", "sh3"), ("SH3 manual.pdf", "sh3")], ingested


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
