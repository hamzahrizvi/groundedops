"""Confluence manuals sync against a fake Confluence.

Nothing reaches Atlassian, the index or the real catalogue: the connection,
ingest, removal and the catalogue are swapped for fakes, and the state file
is a temp file.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import confluence_sync as cs  # noqa: E402

BODY = "<p>" + "Fit the hopper to the rail and tighten both screws. " * 6 + "</p>"


class FakeApi:
    """Website (page) / Smart Hopper 3 (folder) / Installation (page, with a PDF)
       Website / Overview (a short page, not offered)"""
    site = "itl.atlassian.net"
    tree = {
        ("pages", "1"): [{"type": "folder", "id": "10", "title": "Smart Hopper 3"},
                         {"type": "page", "id": "30", "title": "Overview"}],
        ("folders", "10"): [{"type": "page", "id": "20", "title": "Installation"}],
    }
    attachments = {"20": [{"id": "att5", "title": "SH3 manual.pdf", "version": {"number": 2}},
                          {"id": "att6", "title": "photo.png", "version": {"number": 1}}]}
    page_version = 1

    def each(self, path, **params):
        parts = path.split("/")
        if parts[-1] == "direct-children":
            return iter(self.tree.get((parts[-3], parts[-2]), []))
        return iter(self.attachments.get(parts[-2], []))

    def page(self, pid):
        return {"title": {"1": "Website", "20": "Installation", "30": "Overview"}[pid],
                "version": {"number": self.page_version},
                "body": {"storage": {"value": "<p>Hi</p>" if pid == "30" else BODY}}}

    def download(self, page_id, att_id):
        return b"%PDF fake"


class FakeCatalog:
    def __init__(self):
        self.data = {"categories": [{"key": "coin", "name": "Coin Hoppers", "products": [
            {"key": "sh3", "name": "Smart Hopper 3", "sources": []}]}]}

    def load_catalog(self):
        return self.data

    def attach_source(self, cat, prod, source):
        self.data["categories"][0]["products"][0]["sources"].append(source)

    def refile_source(self, source, keys):
        for p in self.data["categories"][0]["products"]:
            p["sources"] = [s for s in p["sources"] if s != source]


class World:
    """Swaps the fakes in for a call, records what reached the index."""
    def __init__(self, state_path):
        self.state, self.ingested, self.removed, self.texts = state_path, [], [], {}
        self.catalog = FakeCatalog()
        self.uploads = []                # documents uploaded by hand, by name

    def _with(self, fn, api=None):
        saved = (cs._connect, cs._ingest, cs._remove, cs.catalog, cs._STATE, cs._uploads)
        cs._connect = lambda: (api, "pages", "1")
        cs._uploads = lambda items: {cs._title_key(u): u for u in self.uploads}
        def ingest(content, source, cat, prod):
            self.ingested.append((source, prod))
            self.texts[source] = content.decode("utf-8", "replace")
        cs._ingest = ingest
        def remove(source):
            self.removed.append(source)
            if source in self.uploads:
                self.uploads.remove(source)
        cs._remove = remove
        cs.catalog = self.catalog
        cs._STATE = self.state
        try:
            return fn()
        finally:
            (cs._connect, cs._ingest, cs._remove, cs.catalog, cs._STATE, cs._uploads) = saved

    def run(self, api, apply=True, check=True):
        return self._with(lambda: cs.run(apply, check), api)

    def items(self):
        return self._with(lambda: {i["title"]: i for i in cs.overview()["items"]})

    def decide(self, titles, **kw):
        keys = [self.items()[t]["key"] for t in titles]
        self._with(lambda: cs.set_items(keys, **kw))


def test_folder_url():
    assert cs.parse_folder_url(
        "https://itl.atlassian.net/wiki/spaces/WEB/folder/123?atlOrigin=x") == ("itl.atlassian.net", "folders", "123")
    assert cs.parse_folder_url(
        "https://itl.atlassian.net/wiki/spaces/CS/pages/1069318147/Website") == ("itl.atlassian.net", "pages", "1069318147")
    assert cs.normalise_folder_url(
        "https://itl.atlassian.net/wiki/spaces/CS/pages/1069318147/Website").endswith("/pages/1069318147")
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


def test_review_mode_excludes_until_approved():
    with tempfile.TemporaryDirectory() as d:
        w, api = World(os.path.join(d, "s.json")), FakeApi()
        w.run(api)
        items = w.items()
        # the Website page itself, the short Overview page and the PNG are not offered
        assert sorted(items) == ["Installation", "SH3 manual.pdf"], sorted(items)
        # not approved is excluded; the first check calls nothing "new"
        assert {i["status"] for i in items.values()} == {"excluded"}
        assert not any(i["is_new"] or i["is_updated"] for i in items.values())
        assert items["Installation"]["product"] == "sh3"      # suggested by its folder
        assert w.ingested == []

        w.decide(["SH3 manual.pdf"], decision="approved")
        w.decide(["Installation"], decision="excluded")
        res = w.run(api)
        assert w.ingested == [("SH3 manual.pdf", "sh3")] and res["added"] == ["SH3 manual.pdf"], res
        items = w.items()
        assert items["SH3 manual.pdf"]["status"] == "synced" and items["Installation"]["status"] == "excluded"

        # nothing changed: nothing re-indexed
        w.run(api)
        assert len(w.ingested) == 1

        # a page edited in Confluence and a file added later are flagged,
        # and the new file stays excluded until approved
        api.page_version = 2
        api.attachments = {"20": FakeApi.attachments["20"]
                           + [{"id": "att7", "title": "SH3 spares.pdf", "version": {"number": 1}}]}
        w.run(api)
        items = w.items()
        assert items["Installation"]["is_updated"] and items["Installation"]["prev_version"] == 1
        assert items["SH3 spares.pdf"]["is_new"] and items["SH3 spares.pdf"]["status"] == "excluded"
        assert len(w.ingested) == 1

        # excluded after syncing: removed from the index at the next sync
        w.decide(["SH3 manual.pdf"], decision="excluded")
        assert w.items()["SH3 manual.pdf"]["status"] == "remove"
        w.run(api)
        assert w.removed == ["SH3 manual.pdf"]


def test_auto_mode_syncs_everything_and_follows_confluence():
    with tempfile.TemporaryDirectory() as d:
        w, api = World(os.path.join(d, "s.json")), FakeApi()
        w._with(lambda: cs.set_mode("auto"))
        w.run(api)
        assert sorted(w.ingested) == [("Installation.txt", "sh3"), ("SH3 manual.pdf", "sh3")], w.ingested

        # a check alone indexes nothing, even when a page changed
        api.page_version = 2
        res = w.run(api, apply=False)
        assert res["changed"] == ["Installation"] and len(w.ingested) == 2, res
        assert w.items()["Installation"]["status"] == "changed"
        w.run(api)
        assert w.ingested[-1] == ("Installation.txt", "sh3")

        # deleted in Confluence: removed, and dropped from the list
        class Gone(FakeApi):
            attachments = {}
        w.run(Gone())
        assert w.removed == ["SH3 manual.pdf"] and "SH3 manual.pdf" not in w.items()

        # Confluence lists nothing (lost access): nothing is removed
        class Empty(FakeApi):
            tree = {}
        res = w.run(Empty())
        assert w.removed == ["SH3 manual.pdf"] and res["errors"], res


def test_old_state_file_is_upgraded():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.json")
        with open(path, "w") as f:
            json.dump({"sources": {"SH3 manual.pdf": "att:att5:2"}}, f)
        w = World(path)
        w.run(FakeApi())
        items = w.items()
        # already synced at version 2: stays approved, not re-downloaded
        assert items["SH3 manual.pdf"]["status"] == "synced" and w.ingested == [], (items, w.ingested)
        assert items["Installation"]["status"] == "excluded"

        # a decision left "pending" by the old page reads as excluded
        st = json.load(open(path))
        st["items"]["page:20"]["decision"] = "pending"
        json.dump(st, open(path, "w"))
        assert w.items()["Installation"]["decision"] == "excluded"


class ManualApi(FakeApi):
    """Website / Manual (page) / Cmd A (a short page) and Cmd B; Manual also
    holds a file, which stays a document of its own when Manual is combined."""
    tree = {("pages", "1"): [{"type": "page", "id": "40", "title": "Manual"}],
            ("pages", "40"): [{"type": "page", "id": "41", "title": "Cmd A"},
                              {"type": "page", "id": "42", "title": "Cmd B"}]}
    attachments = {"40": [{"id": "att9", "title": "Wiring.pdf", "version": {"number": 1}}]}

    def page(self, pid):
        body = {"41": "<p>Send 0x07.</p>"}.get(pid, BODY)
        return {"title": {"1": "Website", "40": "Manual", "41": "Cmd A", "42": "Cmd B"}[pid],
                "version": {"number": 1}, "body": {"storage": {"value": body}}}


def test_combining_a_folder_into_one_document():
    from chunking import SECTION_CLOSE, SECTION_OPEN
    with tempfile.TemporaryDirectory() as d:
        w, api = World(os.path.join(d, "s.json")), ManualApi()
        w._with(lambda: cs.set_mode("auto"))
        w.run(api)
        assert sorted(w.ingested) == [("Cmd B.txt", None), ("Manual.txt", None), ("Wiring.pdf", None)]

        # combine Manual: one document, every page a section in Confluence's
        # order (the short page too), the file left alone
        w._with(lambda: cs.set_bundle(["Manual"], True))
        res = w.run(api)
        items = w.items()
        assert sorted(items) == ["Manual", "Wiring.pdf"], sorted(items)
        assert items["Manual"]["kind"] == "bundle" and items["Manual"]["pages"] == 3
        text = w.texts["Manual.txt"]
        heads = [part.split(SECTION_CLOSE, 1)[0] for part in text.split(SECTION_OPEN)[1:]]
        assert heads == ["Manual", "Cmd A", "Cmd B"], heads
        assert "Send 0x07." in text
        # the pages synced on their own are taken out, before the combined copy goes in
        assert sorted(w.removed) == ["Cmd B.txt", "Manual.txt"], w.removed
        assert res["added"] == ["Manual.txt"], res

        # split again: the pages come back approved, the combined copy goes
        w._with(lambda: cs.set_bundle(["Manual"], False))
        w.run(api)
        items = w.items()
        assert sorted(items) == ["Cmd B", "Manual", "Wiring.pdf"], sorted(items)
        assert all(i["decision"] == "approved" and i["status"] == "synced" for i in items.values())
        assert w.removed[-1] == "Manual.txt"


def test_a_whole_manual_replaces_its_upload():
    with tempfile.TemporaryDirectory() as d:
        w, api = World(os.path.join(d, "s.json")), FakeApi()
        # uploaded by hand and filed under another product than the folder suggests
        w.uploads = ["Installation-v3-170925-134345.pdf"]
        w.catalog.data["categories"][0]["products"].append(
            {"key": "nv9", "name": "NV9", "sources": ["Installation-v3-170925-134345.pdf"]})
        w.run(api, apply=False)
        inst = w.items()["Installation"]
        assert inst["uploaded_as"] == "Installation-v3-170925-134345.pdf" and inst["replaces"], inst
        assert inst["product"] == "nv9" and inst["suggested"]       # the upload's filing, not the folder's

        w.decide(["Installation"], decision="approved")
        res = w.run(api)
        assert ("Installation.txt", "nv9") in w.ingested, w.ingested
        assert w.removed == ["Installation-v3-170925-134345.pdf"] and len(res["replaced"]) == 1, res

        # uploaded again by hand after the first sync: the next sync removes it again
        w.uploads = ["Installation-v4.pdf"]
        res = w.run(api)
        assert w.removed[-1] == "Installation-v4.pdf" and len(res["replaced"]) == 1, res
        assert w.ingested.count(("Installation.txt", "nv9")) == 1       # not re-indexed


def test_a_manual_kept_as_chapters_replaces_nothing_until_combined():
    with tempfile.TemporaryDirectory() as d:
        w, api = World(os.path.join(d, "s.json")), ManualApi()
        w.uploads = ["Manual-v1.pdf", "Cmd B.pdf"]
        w._with(lambda: cs.set_mode("auto"))
        w.run(api, apply=False)
        items = w.items()
        # the Manual page has chapters under it: it is only the cover
        assert items["Manual"]["uploaded_as"] == "Manual-v1.pdf" and not items["Manual"]["replaces"]
        # Keep both: the upload stays even though Cmd B is a whole page
        w.decide(["Cmd B"], keep_upload=True)
        w.run(api)
        assert w.removed == [], w.removed

        # Cmd A is too short to be a document of its own: only a section
        w.uploads.append("Cmd A-v2.pdf")
        w._with(lambda: cs.set_bundle(["Manual"], True))
        w.run(api)
        items = w.items()
        assert items["Manual"]["kind"] == "bundle"
        # the combined manual replaces its own upload and its section's,
        # but not the one marked Keep both before it was combined
        assert "Manual-v1.pdf" in w.removed and "Cmd A-v2.pdf" in w.removed, w.removed
        assert "Cmd B.pdf" not in w.removed, w.removed


def test_sync_now_after_a_recent_check_does_not_read_confluence_again():
    with tempfile.TemporaryDirectory() as d:
        w, api = World(os.path.join(d, "s.json")), FakeApi()
        w.run(api, apply=False)
        w.decide(["Installation"], decision="approved")

        reads = []
        class Counting(FakeApi):
            def each(self, path, **params):
                reads.append(path)
                return FakeApi.each(self, path, **params)
        res = w.run(Counting(), check=None)          # what Sync now sends
        assert reads == [] and res["added"] == ["Installation.txt"] and not res["checked"], res

        # an old check: Sync now reads Confluence first
        st = json.load(open(w.state))
        st["checked_at"] = "2026-01-01T00:00:00+00:00"
        json.dump(st, open(w.state, "w"))
        res = w.run(Counting(), check=None)
        assert reads and res["checked"], res


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
