"""run_tests.py: Playwright files get their own process, tests run on scratch
env paths, and an own-process file that hangs is reported as a failure."""
import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run_tests


def test_playwright_files_own_process():
    assert run_tests.needs_own_process(
        "try:\n    from playwright.sync_api import sync_playwright\n")
    assert not run_tests.needs_own_process('"""runs without playwright"""')


def test_scratch_paths_set():
    src = Path(run_tests.__file__).parent
    assert os.environ["SECRETS_VAULT_PATH"] != str(src / ".secrets.enc")
    assert os.environ["ENV_FILE_PATH"] != str(src / ".env")


def test_timeout_reports_failure():
    old_dir, old_to = run_tests.TESTS_DIR, run_tests.OWN_PROCESS_TIMEOUT
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "test_hang.py").write_text(
            "# run-in-own-process\nimport time\ntime.sleep(30)\n"
            "def test_x():\n    pass\n", encoding="utf-8")
        run_tests.TESTS_DIR, run_tests.OWN_PROCESS_TIMEOUT = Path(d), 1
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                ok = run_tests.discover_and_run()
        finally:
            run_tests.TESTS_DIR, run_tests.OWN_PROCESS_TIMEOUT = old_dir, old_to
    assert ok is False
    assert "timed out" in buf.getvalue()


if __name__ == "__main__":
    for n in list(globals()):
        if n.startswith("test_"):
            globals()[n]()
    print("ok")
