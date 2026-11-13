"""release.py bumps VERSION and drafts the notes; it no longer copies files into src/legacy (D-20261112-20)."""
import os
import sys
import tempfile

import release


def test_read_version_reads_the_version_file_and_stops_without_one():
    saved = release.VERSION_FILE
    with tempfile.TemporaryDirectory() as d:
        release.VERSION_FILE = os.path.join(d, "VERSION")
        try:
            with open(release.VERSION_FILE, "w", encoding="utf-8") as fh:
                fh.write("v17.3\n")
            assert release.read_version() == (17, 3)
            os.remove(release.VERSION_FILE)
            try:
                release.read_version()
            except SystemExit:
                pass
            else:
                raise AssertionError("a missing VERSION must stop the release")
        finally:
            release.VERSION_FILE = saved


def test_a_release_bumps_version_and_writes_no_copy_of_the_files():
    # No monkeypatch fixture: run_tests.py's own runner has no fixtures.
    saved = (release.VERSION_FILE, release.changed_files, release.sh,
             release.subprocess.run, sys.argv)
    calls = []
    with tempfile.TemporaryDirectory() as d:
        release.VERSION_FILE = os.path.join(d, "VERSION")
        release.changed_files = lambda base: ["src/main.py"]
        release.sh = lambda *a: ""
        release.subprocess.run = lambda args, **kw: calls.append(args)
        sys.argv = ["release.py", "--minor"]
        try:
            with open(release.VERSION_FILE, "w", encoding="utf-8") as fh:
                fh.write("17.3\n")
            assert release.main() == 0
            with open(release.VERSION_FILE, encoding="utf-8") as fh:
                assert fh.read().strip() == "17.4"
            assert calls == [["git", "add", "VERSION"]]
            assert not os.path.exists(os.path.join(release.HERE, "legacy"))
        finally:
            (release.VERSION_FILE, release.changed_files, release.sh,
             release.subprocess.run, sys.argv) = saved
