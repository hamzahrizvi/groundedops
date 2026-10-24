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
        self.internal = set()            # documents marked internal

    def _with(self, fn, api=None):
        saved = (cs._connect, cs._ingest, cs._remove, cs.catalog, cs._STATE, cs._uploads,
                 cs._mark_internal)
        def mark(sources, on):
            (self.internal.update if on else self.internal.difference_update)(sources)
        cs._mark_internal = mark
        cs._connect = lambda: (api, "pages", "1")
        cs._uploads = lambda items: {cs._title_key(u): u for u in self.uploads}
        def ingest(content, source, cat, prod):
            self.ingested.append((source, prod))
            self.texts[source] = content.decode("utf-8", "replace")
        cs._ingest = ingest
        def remove(source):
            self.removed.append(source)
            self.internal.discard(source)
            if source in self.uploads:
                self.uploads.remove(source)
        cs._remove = remove
        cs.catalog = self.catalog
        cs._STATE = self.state
        try:
            return fn()
        finally:
            (cs._connect, cs._ingest, cs._remove, cs.catalog, cs._STATE, cs._uploads,
             cs._mark_internal) = saved

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
    assert "Note" in t and "\nA | B" in t


def test_page_text_keeps_a_table_row_on_one_line():
    # Confluence wraps every cell in <p>: rows came out one cell per line.
    t = cs.page_text("T", '<table><tbody><tr><th><p>Supply Current</p></th><th><p>Maximum</p></th></tr>'
                     '<tr><td><p>Peak</p></td><td><p>1.50A</p><p>at 12V</p></td></tr></tbody></table>'
                     '<ac:adf-extension><ac:adf-node type="panel">'
                     '<ac:adf-attribute key="panel-type">note</ac:adf-attribute>'
                     '<ac:adf-attribute key="local-id">baad7fce-8302</ac:adf-attribute>'
                     '<ac:adf-content><p>Check the PSU.</p></ac:adf-content></ac:adf-node>'
                     '<ac:adf-fallback><p>Check the PSU.</p></ac:adf-fallback></ac:adf-extension>')
    assert "Supply Current | Maximum\nPeak | 1.50A at 12V\n" in t
    assert t.count("Check the PSU.") == 1 and "baad7fce" not in t and "note" not in t


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


class MovableApi(ManualApi):
    """ManualApi whose Manual page can be renamed, or moved into an Archive folder."""
    def __init__(self, name="Manual", archived=False):
        self.name = name
        manual = {"type": "page", "id": "40", "title": name}
        self.tree = {**ManualApi.tree, ("pages", "1"): [
            {"type": "folder", "id": "50", "title": "Archive"} if archived else manual]}
        if archived:
            self.tree[("folders", "50")] = [manual]

    def page(self, pid):
        return {**ManualApi.page(self, pid), **({"title": self.name} if pid == "40" else {})}


def _combined_and_synced(w):
    """Review mode: Manual combined, approved, filed under sh3 and synced."""
    w.run(MovableApi(), apply=False)
    w._with(lambda: cs.set_bundle(["Manual"], True))
    w.run(MovableApi(), apply=False)
    w.decide(["Manual"], decision="approved", product="sh3")
    w.run(MovableApi())
    assert ("Manual.txt", "sh3") in w.ingested, w.ingested


def _still_the_same_document(w, title, path):
    items = w.items()
    assert sorted(items) == [title, "Wiring.pdf"], sorted(items)
    m = items[title]
    assert m["key"] == "bundle:id:40" and m["source"] == "Manual.txt" and m["path"] == path, m
    assert (m["decision"], m["product"], m["status"]) == ("approved", "sh3", "synced"), m
    assert w.removed == [] and w.ingested.count(("Manual.txt", "sh3")) == 1, (w.removed, w.ingested)
    assert path + [title] in w._with(lambda: cs.overview()["bundles"])


def test_a_combined_folder_survives_a_rename():
    with tempfile.TemporaryDirectory() as d:
        w = World(os.path.join(d, "s.json"))
        _combined_and_synced(w)
        w.run(MovableApi("SSP Manual"))
        _still_the_same_document(w, "SSP Manual", [])


def test_a_combined_folder_survives_a_move():
    with tempfile.TemporaryDirectory() as d:
        w = World(os.path.join(d, "s.json"))
        _combined_and_synced(w)
        w.run(MovableApi(archived=True))
        _still_the_same_document(w, "Manual", ["Archive"])
        # split again from where it is now: its pages take its decision
        w._with(lambda: cs.set_bundle(["Archive", "Manual"], False))
        w.run(MovableApi(archived=True), apply=False)
        items = w.items()
        assert sorted(items) == ["Cmd B", "Manual", "Wiring.pdf"], sorted(items)
        assert items["Cmd B"]["decision"] == items["Manual"]["decision"] == "approved", items


def test_a_title_path_bundle_is_pinned_to_its_id():
    with tempfile.TemporaryDirectory() as d:
        w = World(os.path.join(d, "s.json"))
        _combined_and_synced(w)
        # as a state file written before bundles had ids, plus one whose
        # folder is no longer there
        st = json.load(open(w.state))
        st["bundles"] = [["Manual"], ["Gone folder"]]
        st["items"]["bundle:Manual"] = {**st["items"].pop("bundle:id:40"), "id": "bundle:Manual"}
        json.dump(st, open(w.state, "w"))
        w.run(MovableApi())
        _still_the_same_document(w, "Manual", [])
        assert json.load(open(w.state))["bundles"] == [{"id": "40", "path": ["Manual"]},
                                                       {"path": ["Gone folder"]}]
        # and from now on a rename keeps it
        w.run(MovableApi("SSP Manual"))
        assert w.items()["SSP Manual"]["key"] == "bundle:id:40" and w.removed == []


class ChapterApi(FakeApi):
    """Website / Smart Hopper 3 (folder) / SH3 User Manual (page) / three chapters."""
    tree = {("pages", "1"): [{"type": "folder", "id": "10", "title": "Smart Hopper 3"}],
            ("folders", "10"): [{"type": "page", "id": "60", "title": "SH3 User Manual"}],
            ("pages", "60"): [{"type": "page", "id": str(n), "title": f"Chapter {n}"}
                              for n in (61, 62, 63)]}
    attachments = {}

    def page(self, pid):
        title = {"1": "Website", "60": "SH3 User Manual"}.get(pid, f"Chapter {pid}")
        return {"title": title, "version": {"number": 1}, "body": {"storage": {"value": BODY}}}


def test_a_manual_kept_in_chapters_is_combined_on_its_own():
    with tempfile.TemporaryDirectory() as d:
        w, api = World(os.path.join(d, "s.json")), ChapterApi()
        w._with(lambda: cs.set_mode("auto"))
        w.run(api)
        assert len(w.ingested) == 4 and "SH3 User Manual" in w.items()

        # chapters that disagree (one internal) are left as they are
        w.decide(["Chapter 62"], decision="internal")
        w.run(api)
        assert w.items()["SH3 User Manual"]["kind"] == "page" and w.removed == []

        # agreeing again: the next check combines them, keeping decision and product
        w.decide(["Chapter 62"], decision="approved")
        w.run(api)
        m = w.items()["SH3 User Manual"]
        assert m["kind"] == "bundle" and m["pages"] == 4 and m["decision"] == "approved", m
        assert w.ingested[-1] == ("SH3 User Manual.txt", "sh3") and len(w.removed) == 4, w.ingested

        # split by someone: stays split
        w._with(lambda: cs.set_bundle(["Smart Hopper 3", "SH3 User Manual"], False))
        w.run(api)
        w.run(api)
        assert w.items()["SH3 User Manual"]["kind"] == "page" and len(w.items()) == 4


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


def test_internal_documents_sync_but_never_reach_customers():
    with tempfile.TemporaryDirectory() as d:
        w, api = World(os.path.join(d, "s.json")), FakeApi()
        w.run(api, apply=False)
        w.decide(["Installation"], decision="internal")
        w.decide(["SH3 manual.pdf"], decision="approved")
        w.run(api)
        assert sorted(s for s, _ in w.ingested) == ["Installation.txt", "SH3 manual.pdf"]
        assert w.internal == {"Installation.txt"}
        items = w.items()
        assert items["Installation"]["decision"] == "internal" and items["Installation"]["status"] == "synced"

        # internal <-> approved on a synced document: a flag, no re-index
        w.decide(["Installation"], decision="approved")
        assert w.internal == set() and len(w.ingested) == 2
        w.decide(["Installation", "SH3 manual.pdf"], decision="internal")
        assert w.internal == {"Installation.txt", "SH3 manual.pdf"}

        # excluded: removed at the next sync, and the mark goes with it
        w.decide(["SH3 manual.pdf"], decision="excluded")
        w.run(api)
        assert "SH3 manual.pdf" in w.removed and w.internal == {"Installation.txt"}


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


def test_a_deleted_product_does_not_fail_the_sync():
    with tempfile.TemporaryDirectory() as d:
        w, api = World(os.path.join(d, "s.json")), FakeApi()
        w.run(api, apply=False)
        w.decide(["Installation"], decision="approved", product="gone_product")
        res = w.run(api)
        assert ("Installation.txt", None) in w.ingested, w.ingested
        assert w.items()["Installation"]["status"] == "synced"
        assert any("gone_product no longer exists" in e for e in res["errors"]), res


class InternalTitlesApi(FakeApi):
    attachments = {"20": [{"id": "a1", "title": "X (Internal).pdf", "version": {"number": 1}},
                          {"id": "a2", "title": "INTERNAL - Y.pdf", "version": {"number": 1}},
                          {"id": "a3", "title": "Z internals.pdf", "version": {"number": 1}},
                          {"id": "a4", "title": "W_internal.pdf", "version": {"number": 1}}]}


def test_a_title_that_says_internal_is_filed_internal_in_auto_mode():
    with tempfile.TemporaryDirectory() as d:
        w, api = World(os.path.join(d, "s.json")), InternalTitlesApi()
        w._with(lambda: cs.set_mode("auto"))
        w.run(api)
        items = w.items()
        got = {t: items[t]["decision"] for t in ("X (Internal).pdf", "INTERNAL - Y.pdf",
                                                 "Z internals.pdf", "W_internal.pdf", "Installation")}
        assert got == {"X (Internal).pdf": "internal", "INTERNAL - Y.pdf": "internal",
                       "Z internals.pdf": "approved", "W_internal.pdf": "internal",
                       "Installation": "approved"}, got
        # synced, but marked before it went in, so customers never get it
        assert {"X (Internal).pdf", "INTERNAL - Y.pdf", "W_internal.pdf"} <= {s for s, _ in w.ingested}
        assert w.internal == {"X (Internal).pdf", "INTERNAL - Y.pdf", "W_internal.pdf"}, w.internal
        # an admin's approval stands at the next check: the rule files a first sighting only
        w.decide(["X (Internal).pdf"], decision="approved")
        w.run(api)
        assert w.items()["X (Internal).pdf"]["decision"] == "approved"
        assert "X (Internal).pdf" not in w.internal

    with tempfile.TemporaryDirectory() as d:     # review mode: still excluded until approved
        w = World(os.path.join(d, "s.json"))
        w.run(InternalTitlesApi(), apply=False)
        assert w.items()["X (Internal).pdf"]["decision"] == "excluded"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)


class StoppingApi(FakeApi):
    """Asks the run to stop the first time a document is fetched."""
    stop = False

    def page(self, pid):
        if self.stop:
            cs.cancel()
        return super().page(pid)

    def download(self, page_id, att_id):
        if self.stop:
            cs.cancel()
        return super().download(page_id, att_id)


def test_stopping_a_sync_keeps_what_was_done():
    with tempfile.TemporaryDirectory() as d:
        w, api = World(os.path.join(d, "s.json")), StoppingApi()
        w.run(api, apply=False)
        w.decide(["Installation", "SH3 manual.pdf"], decision="approved")
        api.stop = True
        res = w.run(api, check=False)
        # the document being fetched finishes; the other waits
        assert res.get("cancelled") and res["left"] == 1, res
        assert len(w.ingested) == 1, w.ingested
        statuses = sorted(i["status"] for i in w.items().values())
        assert statuses == ["new", "synced"], statuses
        hist = w._with(lambda: cs._load()["history"][-1])
        assert hist["cancelled"] and hist["left"] == 1, hist
        # the next sync picks up the rest
        api.stop = False
        w.run(api, check=False)
        assert len(w.ingested) == 2


def test_stopping_a_check_changes_nothing():
    with tempfile.TemporaryDirectory() as d:
        w, api = World(os.path.join(d, "s.json")), StoppingApi()
        api.stop = True
        res = w.run(api, apply=True)
        assert res.get("cancelled") and res["kind"] == "sync", res
        assert w.items() == {} and w.ingested == []
        assert not cs.cancel()                      # nothing running now


def test_schedule():
    from datetime import datetime, timedelta
    with tempfile.TemporaryDirectory() as d:
        w = World(os.path.join(d, "s.json"))
        for bad in [("sometimes",), ("hours", 0), ("daily", 24, "25:00"), ("weekly", 24, "02:00", 9)]:
            try:
                w._with(lambda: cs.set_schedule(*bad))
            except ValueError:
                continue
            raise AssertionError(f"accepted {bad}")
        w._with(lambda: cs.set_schedule("daily", at="02:00"))
        st = w._with(cs._load)
        assert st["schedule"]["every"] == "daily"
        now = datetime(2026, 10, 23, 2, 30).astimezone()
        assert cs._due(st, now)                                     # today's 02:00 not run yet
        st["auto_run_at"] = now.isoformat()
        assert not cs._due(st, now)                                 # ran
        assert not cs._due(st, now + timedelta(hours=20))           # tomorrow 02:00 not reached
        assert cs._due(st, now + timedelta(hours=24))               # tomorrow's slot
        st["auto_run_at"] = (now - timedelta(days=3)).isoformat()
        assert not cs._due(st, now + timedelta(hours=13))           # a missed slot only runs the same day
        w._with(lambda: cs.set_schedule("weekly", at="02:00", day=4))  # Friday
        st = w._with(cs._load)
        fri = datetime(2026, 10, 23, 3, 0).astimezone()             # a Friday
        assert cs._due(st, fri) and not cs._due(st, fri - timedelta(days=1))
        w._with(lambda: cs.set_schedule("off"))
        assert not cs._due(w._with(cs._load), fri) and w._with(cs._next_run) is None


def test_a_folder_named_after_a_product_and_more_suggests_it():
    with tempfile.TemporaryDirectory() as d:
        w = World(os.path.join(d, "s.json"))
        w.catalog.data["categories"][0]["products"] += [
            {"key": "mc", "name": "MyCheckr", "sources": []},
            {"key": "mcm", "name": "MyCheckr mini", "aliases": ["MyCheckr Mini"], "sources": []},
            {"key": "sh", "name": "SMART Coin System", "aliases": ["SMART Hopper"], "sources": []}]
        sug = lambda path: w._with(lambda: cs._suggest(path))[1]
        assert sug(["MyCheckr Range Documentation", "MyCheckr Mini User Manual",
                    "MyCheckr Mini Product Compliance & Approvals"]) == "mcm"
        assert sug(["MyCheckr Range Documentation", "MyCheckr User Manual"]) == "mc"
        assert sug(["SMART Hopper 3 Documentation", "SMART Hopper 3 User Manual"]) == "sh3"
        assert sug(["Smart Hopper 3"]) == "sh3"
        assert sug(["Products", "Other things"]) == ""


def test_review_mode_holds_an_edited_document_until_approved():
    with tempfile.TemporaryDirectory() as d:
        w, api = World(os.path.join(d, "s.json")), FakeApi()
        w.run(api)
        w.decide(["Installation"], decision="approved")
        w.run(api)
        assert w.ingested == [("Installation.txt", "sh3")], w.ingested
        api.page_version = 2                       # edited in Confluence
        res = w.run(api)
        it = w.items()["Installation"]
        assert it["status"] == "held" and it["is_updated"], it
        assert len(w.ingested) == 1 and w.removed == [], (w.ingested, w.removed)  # old version answers
        assert res["held"] == ["Installation.txt"], res
        w.run(api)
        assert len(w.ingested) == 1                # still waiting
        w.decide(["Installation"], decision="approved")
        assert w.items()["Installation"]["status"] == "changed"
        w.run(api)
        assert len(w.ingested) == 2 and w.items()["Installation"]["status"] == "synced"
        api.page_version = 3                       # auto mode: edits sync without a hold
        w._with(lambda: cs.set_mode("auto"))
        w.run(api)
        assert len(w.ingested) == 3 and w.items()["Installation"]["status"] == "synced"


def test_updates_found_before_holds_existed_wait_too():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.json")
        json.dump({"mode": "review", "items": {"page:20": {
            "kind": "page", "id": "20", "title": "Installation", "source": "Installation.txt",
            "path": [], "present": True, "decision": "approved",
            "version": 2, "synced_version": 1}}}, open(path, "w"))
        assert World(path).items()["Installation"]["status"] == "held"


class BigFileApi(FakeApi):
    attachments = {"20": [{"id": "att8", "title": "Huge.pdf", "version": {"number": 1},
                           "fileSize": 200 * 1024 * 1024}]}


def test_an_oversized_attachment_is_skipped_and_named():
    with tempfile.TemporaryDirectory() as d:
        w, api = World(os.path.join(d, "s.json")), BigFileApi()
        w._with(lambda: cs.set_mode("auto"))
        res = w.run(api)
        assert ("Huge.pdf", "sh3") not in w.ingested, w.ingested
        assert any(e.startswith("Huge.pdf:") and "limit" in e for e in res["errors"]), res
        assert w.items()["Huge.pdf"]["status"] == "error"
        saved, cs.MAX_FILE_BYTES = cs.MAX_FILE_BYTES, 4   # no listed size: checked after download
        try:
            res = w.run(FakeApi())
        finally:
            cs.MAX_FILE_BYTES = saved
        assert any(e.startswith("SH3 manual.pdf:") for e in res["errors"]), res


def test_only_root_switches_to_auto():
    import routes_confluence as rc
    from fastapi import HTTPException

    def refuse(_):
        raise HTTPException(status_code=403, detail="root only")
    saved = rc._require_root, rc._require_admin, rc._status, cs.set_mode
    modes = []
    rc._require_root, rc._require_admin = refuse, lambda _: {"email": "a@x"}
    rc._status, cs.set_mode = (lambda: {}), modes.append
    try:
        try:
            rc.admin_confluence_mode(rc.ModeReq(mode="auto"), "pw")
            assert False, "an admin switched to auto"
        except HTTPException as e:
            assert e.status_code == 403
        rc.admin_confluence_mode(rc.ModeReq(mode="review"), "pw")
        assert modes == ["review"], modes
    finally:
        rc._require_root, rc._require_admin, rc._status, cs.set_mode = saved
