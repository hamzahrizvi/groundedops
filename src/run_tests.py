#!/usr/bin/env python3
"""
Minimal test runner — discovers and runs every test_* function in
tests/test_*.py without requiring pytest to be installed.

Usage: python3 run_tests.py        (TEST_JOBS=1 to run one file at a time)
(pytest works too, if installed: pip install pytest && pytest tests/)
"""

import atexit
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Keep in-process test files off the real src/.env and src/.secrets.enc.
# setdefault: a file or caller that sets its own path still wins.
_SCRATCH = tempfile.mkdtemp(prefix="go-tests-")
atexit.register(shutil.rmtree, _SCRATCH, ignore_errors=True)
_SCRATCH_FILES = {"ENV_FILE_PATH": "test.env", "SECRETS_VAULT_PATH": "secrets.enc",
                  "DOC_KINDS_PATH": "doc_kinds.json"}
for _name, _file in _SCRATCH_FILES.items():
    os.environ.setdefault(_name, os.path.join(_SCRATCH, _file))

OWN_PROCESS_TIMEOUT = 600
# Own-process files are separate interpreters, so they run side by side: 121
# of the 150 files. Measured 2026-11-12 on an idle 12-thread box, same 414
# results each time: 466 s one at a time, 253 s at 4, 147 s at 8.
# TEST_JOBS=1 runs them one at a time, as before.
JOBS = max(1, int(os.getenv("TEST_JOBS") or min(8, os.cpu_count() or 1)))
# Name endings an own-process child must see blank. main.py loads src/.env for
# any key not already in the environment ("" counts as present), so blanking
# every secret-shaped name -- from the parent AND from src/.env -- keeps the
# real secrets out of the child, including names added to .env later.
SECRET_SUFFIXES = ("_KEY", "_SECRET", "_PASSWORD", "_PASSPHRASE", "_TOKEN")
MAIN_ENV_FILE = Path(__file__).parent / ".env"  # the file main.py loads


def secret_names():
    """Secret-shaped names in os.environ or in src/.env (key names only)."""
    names = set(os.environ)
    try:
        for raw in MAIN_ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                names.add(line.partition("=")[0].strip())
    except OSError:
        pass
    return {n for n in names if n.upper().endswith(SECRET_SUFFIXES)}


TESTS_DIR = Path(__file__).parent / "tests"
ISOLATED_RUNNER = TESTS_DIR / "_run_isolated.py"


def own_process_env():
    """Environment for an own-process test child: secrets blanked, PYTHONPATH set."""
    env = dict(os.environ)
    env.update({k: "" for k in secret_names()})
    # Its own copy of each scratch path this runner set (a caller's own path is
    # left alone): files running at the same time must not share one test.env,
    # and a file no longer starts on what the file before it wrote there.
    own = tempfile.mkdtemp(dir=_SCRATCH)
    for name, file in _SCRATCH_FILES.items():
        if os.environ.get(name) == os.path.join(_SCRATCH, file):
            env[name] = os.path.join(own, file)
    # PYTHONPATH, because a subprocess puts sys.path[0] at the SCRIPT's
    # directory (tests/), not this one -- _harness.py lives beside run_tests.py.
    here = str(Path(__file__).parent)
    env["PYTHONPATH"] = here + os.pathsep + env.get("PYTHONPATH", "")
    return env


class SkipTest(Exception):
    """Raise this inside a test to mark it skipped (e.g. a required
    optional dependency like sentence-transformers isn't installed in
    this environment) rather than disguising the skip as a pass."""
    pass


def needs_own_process(source):
    """True when a test file must run in a fresh interpreter.

    Shared with tests/conftest.py so pytest and this runner draw the
    process boundary in the same place."""
    # These modules eventually load native ML packages. Removing them
    # from sys.modules and importing them again in this interpreter can
    # raise "cannot load module more than once per process". Harness tests
    # also replace whole application modules. Both need a real process
    # boundary, and the isolated helper CALLS test_* functions (executing
    # the file directly did not, so function-style tests falsely passed).
    return (
        "import _harness" in source
        or "import grounding" in source
        or "from grounding import" in source
        or "import router" in source
        or "from router import" in source
        # ingest pulls the PDF and embedding stack, which is the same
        # "cannot load module more than once per process" case.
        or "import ingest" in source
        or "from ingest import" in source
        # retrieval_db pulls chromadb (via db) and sentence-transformers
        # (via embeddings) -- the same native-module case as the others
        # in this list, not a new rule.
        or "import retrieval_db" in source
        or "from retrieval_db import" in source
        # Explicit opt-in, for a file that needs a process boundary for
        # a reason the import list cannot see. test_model_routing sets
        # ENV_FILE_PATH at import so it can never write to the real
        # src/.env -- and in-process that leaked, because own-process
        # tests are spawned with env=dict(os.environ) and _harness sets
        # its own scratch path with setdefault, which then declines to
        # override the leaked one. Five unrelated files failed on
        # "Sign-in required" before this was added.
        or "# run-in-own-process" in source
        # main imports the whole application -- llm, keystore, quota, the
        # widget router -- and loads the real .env. A file that imports it
        # WITHOUT the harness leaves all of that in sys.modules for
        # whatever runs next in the same process. Caught when a test that
        # only inspected main's source made test_llm, test_keystore and
        # test_widget_export start failing, all three of which pass alone.
        or "import main" in source
        or "from main import" in source
        # Playwright can hang the shared interpreter; match real imports
        # (also indented ones), not the word in a docstring.
        or re.search(r"^\s*(from|import)\s+playwright\b", source, re.MULTILINE)
    )


def _run_own_process(path):
    """One own-process file -> (verdict, note, output); verdict is PASS, SKIP
    or FAIL. These files exit non-zero on failure, so the return code is the
    result."""
    try:
        proc = subprocess.run([sys.executable, str(ISOLATED_RUNNER), str(path)],
                              cwd=str(Path(__file__).parent), env=own_process_env(),
                              capture_output=True, text=True,
                              timeout=OWN_PROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        return ("FAIL", "own process, timed out",
                f"(timed out) after {OWN_PROCESS_TIMEOUT}s")
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        if re.search(r"^SKIP(?::| )", out, re.MULTILINE):
            return "SKIP", "own process", out
        return "PASS", "own process", out
    # A missing OPTIONAL dependency is a skip here too. The in-process branch
    # has said so since it was written, but this one only sees a non-zero
    # exit code, so a file needing BOTH its own process and an optional
    # package could never skip -- it could only fail.
    #
    # test_streaming_and_tables is exactly that file: it needs isolation
    # because it loads the real NLI model, and that model needs
    # sentence-transformers, which CI's `unit` job deliberately does not
    # install. So it failed CI while passing locally, and the two paths
    # disagreed about the same policy.
    _m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", out)
    if _m:
        return "SKIP", f"needs {_m.group(1)}, own process", out
    return "FAIL", "own process", out.strip()


def discover_and_run():
    test_files = sorted(TESTS_DIR.glob("test_*.py"))
    total, passed, skipped, failed = 0, 0, 0, []
    load_errors = []

    # The own-process files first, JOBS at a time; their results are reported
    # below in file order, between the in-process files, as they always were.
    # Nothing in-process runs meanwhile: those tests swap sys.modules around.
    own = [p for p in test_files
           if needs_own_process(p.read_text(encoding="utf-8", errors="ignore"))]
    if own:
        print(f"  ({len(own)} own-process files, {JOBS} at a time; results follow in file order)",
              flush=True)
    with ThreadPoolExecutor(max_workers=JOBS) as pool:
        own_results = dict(zip(own, pool.map(_run_own_process, own)))

    for path in test_files:
        # _harness.py stubs real modules into sys.modules (text_utils,
        # structure, llm, db, ...) so the API tests can run without the ML
        # dependencies. Every test file shares one interpreter, so those stubs
        # used to outlive the file that installed them: test_new_routes imports
        # _harness, and then test_regression_bugs, test_router, test_rrf,
        # test_structure and test_text_utils -- every one alphabetically later
        # -- imported the STUB instead of the real module and died with
        # "cannot import name ... (unknown location)", because a
        # types.ModuleType has no __file__.
        #
        # Five files' worth of coverage was skipped on an alphabetical
        # accident, and silently: the COULD NOT LOAD path below left the exit
        # code at 0, so CI reported green. Snapshot the registry per file and
        # restore it afterwards.
        # Some files are integration tests, not unit tests: tests/_harness.py
        # replaces whole modules in sys.modules (db, llm, embeddings, ...) so
        # the API can be exercised without the ML stack. Restoring a dict
        # snapshot afterwards is not enough to undo that -- framework modules
        # get imported, deleted and re-imported around it, and on Python 3.11
        # that left starlette's TestClient returning "No response returned."
        # for test_new_routes.py, but ONLY when something else had run first.
        # It passed standalone and on 3.14, which is exactly the kind of
        # order- and version-dependent flake that wastes an afternoon.
        #
        # A process is the honest isolation boundary for a test that rewrites
        # the module registry. These files exit non-zero on failure, so the
        # return code is the result.
        if path in own_results:
            total += 1
            verdict, note, out = own_results[path]
            print(f"  {verdict}  {path.stem}  ({note})")
            if verdict == "PASS":
                passed += 1
            elif verdict == "SKIP":
                skipped += 1
            else:
                failed.append((path.stem, "(own process)", out))
            continue

        saved_modules = sys.modules.copy()
        saved_path = list(sys.path)
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            sys.path.insert(0, str(Path(__file__).parent))
            module.SkipTest = SkipTest
            try:
                spec.loader.exec_module(module)
            except SkipTest as e:
                # A module can decline to run for a reason no import check
                # can see: test_ui needs a backend LISTENING, not just
                # playwright installed. Before this, installing playwright
                # turned that clean skip into a load error, and a load error
                # fails the whole run.
                skipped += 1
                print(f"  SKIP  {path.stem}  ({e})")
                continue
            except ModuleNotFoundError as e:
                # A missing OPTIONAL dependency (playwright for the UI tests)
                # is a skip, not a failure -- otherwise a dev without it can
                # never get a green run. A broken import inside a module that
                # IS installed still raises ImportError and is caught below.
                skipped += 1
                print(f"  SKIP  {path.stem}  (needs {e.name})")
                continue
            except Exception as e:
                print(f"  COULD NOT LOAD {path.name}: {e}")
                load_errors.append((path.name, str(e)))
                continue

            test_funcs = [
                getattr(module, name) for name in dir(module)
                if name.startswith("test_") and callable(getattr(module, name))
            ]
            setup = getattr(module, "setup_function", None)
            teardown = getattr(module, "teardown_function", None)

            for func in test_funcs:
                total += 1
                try:
                    if setup:
                        setup(func)
                    func()
                    if teardown:
                        teardown(func)
                    passed += 1
                    print(f"  PASS  {path.stem}::{func.__name__}")
                except SkipTest as e:
                    skipped += 1
                    print(f"  SKIP  {path.stem}::{func.__name__}  ({e})")
                except Exception:
                    failed.append((path.stem, func.__name__, traceback.format_exc()))
                    print(f"  FAIL  {path.stem}::{func.__name__}")
        finally:
            sys.modules.clear()
            sys.modules.update(saved_modules)
            sys.path[:] = saved_path

    print(f"\n{'=' * 60}")
    print(f"  {passed}/{total} passed, {skipped} skipped, {len(failed)} failed"
          + (f", {len(load_errors)} could not load" if load_errors else ""))
    print(f"{'=' * 60}")

    if load_errors:
        # Reported as its own category so a file that never ran cannot be
        # mistaken for a file with nothing to run.
        print("\nCOULD NOT LOAD:\n")
        for name, err in load_errors:
            print(f"  {name}: {err}")

    if failed:
        print("\nFAILURE DETAILS:\n")
        for fname, tname, tb in failed:
            print(f"--- {fname}::{tname} ---")
            print(tb)

    # A file that cannot be imported is a failure, not a curiosity. An
    # unimportable test file used to leave this returning True.
    return not failed and not load_errors


if __name__ == "__main__":
    success = discover_and_run()
    sys.exit(0 if success else 1)
