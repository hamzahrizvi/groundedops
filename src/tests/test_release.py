"""release.py's drop mirrors repo paths, so files sharing a basename both survive."""
import os
import tempfile
import zipfile

import release


def test_snapshot_and_zip_keep_files_that_share_a_basename():
    # No tmp_path fixture: run_tests.py's own runner has no fixtures.
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as dest:
        os.makedirs(os.path.join(root, "tools"))
        for rel, body in (("README.md", "root readme"),
                          ("tools/README.md", "tools readme")):
            with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
                fh.write(body)

        release.snapshot(["README.md", "tools/README.md"], root, dest)

        with open(os.path.join(dest, "README.md"), encoding="utf-8") as fh:
            assert fh.read() == "root readme"
        with open(os.path.join(dest, "tools", "README.md"), encoding="utf-8") as fh:
            assert fh.read() == "tools readme"

        with zipfile.ZipFile(release.zip_drop(dest)) as z:
            assert sorted(z.namelist()) == ["README.md", "tools/README.md"]
            assert z.read("tools/README.md") == b"tools readme"
