"""Make `pytest tests/` draw the same process boundary as run_tests.py.

tests/_harness.py replaces real modules in sys.modules with stubs (db, llm,
text_utils, ...). pytest imports every test module into ONE interpreter at
collection time, so once test_accounts imported the harness, test_chunking
imported the stubbed text_utils and died with "cannot import name
'LIST_LINE_RE' from 'text_utils' (unknown location)". run_tests.py has run
those files in their own process for a long time; pytest never did.

Files that need isolation (run_tests.needs_own_process decides, so the two
cannot drift) are collected as a single item that runs tests/_run_isolated.py
in a subprocess. Everything else is collected by pytest as normal.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from run_tests import ISOLATED_RUNNER, needs_own_process  # noqa: E402


class SkipTest(pytest.skip.Exception):
    """run_tests.py injects a SkipTest into every module it loads, and
    test_ui raises it at import when no backend is listening. Under pytest
    that name did not exist, so a clean skip became a NameError that aborted
    collection. This one is pytest's own skip, allowed at module level."""

    def __init__(self, msg=""):
        super().__init__(str(msg), allow_module_level=True)


@pytest.hookimpl(tryfirst=True)
def pytest_pycollect_makemodule(module_path, parent):
    # Stop pytest importing an isolated file in-process as well: importing it
    # is exactly what installs the stubs.
    if module_path.name.startswith("test_"):
        source = module_path.read_text(encoding="utf-8", errors="ignore")
        if needs_own_process(source):
            return _Skipped.from_parent(parent, path=module_path)
    return _Module.from_parent(parent, path=module_path)


class _Module(pytest.Module):
    def _getobj(self):
        # Make SkipTest resolvable while the module body runs.
        import builtins
        builtins.SkipTest = SkipTest
        return super()._getobj()


def pytest_collect_file(parent, file_path):
    if file_path.suffix != ".py" or not file_path.name.startswith("test_"):
        return None
    source = file_path.read_text(encoding="utf-8", errors="ignore")
    if needs_own_process(source):
        return IsolatedFile.from_parent(parent, path=file_path)
    return None


class _Skipped(pytest.File):
    def collect(self):
        return []


class IsolatedFile(pytest.File):
    def collect(self):
        yield IsolatedItem.from_parent(self, name="own_process")


class IsolatedItem(pytest.Item):
    def runtest(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run([sys.executable, str(ISOLATED_RUNNER),
                               str(self.path)],
                              cwd=str(SRC_DIR), env=env,
                              capture_output=True, text=True)
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0:
            if re.search(r"^SKIP(?::| )", out, re.MULTILINE):
                pytest.skip(out.strip().splitlines()[-1])
            return
        # Same policy as run_tests.py: a missing optional dependency skips.
        missing = re.search(r"ModuleNotFoundError: No module named '([^']+)'",
                            out)
        if missing:
            pytest.skip(f"needs {missing.group(1)}")
        raise IsolatedFailure(out.strip())

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, IsolatedFailure):
            return str(excinfo.value)
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, None, f"{self.path.name} (own process)"


class IsolatedFailure(Exception):
    pass
