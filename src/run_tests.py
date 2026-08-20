#!/usr/bin/env python3
"""
Minimal test runner — discovers and runs every test_* function in
tests/test_*.py without requiring pytest to be installed.

Usage: python3 run_tests.py
(pytest works too, if installed: pip install pytest && pytest tests/)
"""

import importlib.util
import sys
import traceback
from pathlib import Path

TESTS_DIR = Path(__file__).parent / "tests"


class SkipTest(Exception):
    """Raise this inside a test to mark it skipped (e.g. a required
    optional dependency like sentence-transformers isn't installed in
    this environment) rather than disguising the skip as a pass."""
    pass


def discover_and_run():
    test_files = sorted(TESTS_DIR.glob("test_*.py"))
    total, passed, skipped, failed = 0, 0, 0, []
    load_errors = []

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
        saved_modules = sys.modules.copy()
        saved_path = list(sys.path)
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            sys.path.insert(0, str(Path(__file__).parent))
            module.SkipTest = SkipTest
            try:
                spec.loader.exec_module(module)
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
