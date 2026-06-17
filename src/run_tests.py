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


def discover_and_run():
    test_files = sorted(TESTS_DIR.glob("test_*.py"))
    total, passed, failed = 0, 0, []

    for path in test_files:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(Path(__file__).parent))
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            print(f"  COULD NOT LOAD {path.name}: {e}")
            continue

        test_funcs = [
            getattr(module, name) for name in dir(module)
            if name.startswith("test_") and callable(getattr(module, name))
        ]

        for func in test_funcs:
            total += 1
            try:
                func()
                passed += 1
                print(f"  PASS  {path.stem}::{func.__name__}")
            except Exception:
                failed.append((path.stem, func.__name__, traceback.format_exc()))
                print(f"  FAIL  {path.stem}::{func.__name__}")

    print(f"\n{'=' * 60}")
    print(f"  {passed}/{total} passed, {len(failed)} failed")
    print(f"{'=' * 60}")

    if failed:
        print("\nFAILURE DETAILS:\n")
        for fname, tname, tb in failed:
            print(f"--- {fname}::{tname} ---")
            print(tb)

    return len(failed) == 0


if __name__ == "__main__":
    success = discover_and_run()
    sys.exit(0 if success else 1)