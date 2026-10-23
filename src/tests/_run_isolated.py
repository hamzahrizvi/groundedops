"""Execute one test module in a fresh interpreter, including test functions.

Some integration tests replace application modules in ``sys.modules`` and
some unit tests import native ML packages that cannot safely be re-imported
after the minimal suite runner restores its module snapshot. This helper is
the process boundary for both cases.
"""
import importlib.util
import sys
import traceback
from pathlib import Path


class SkipTest(Exception):
    pass


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: _run_isolated.py TEST_FILE", file=sys.stderr)
        return 2

    path = Path(sys.argv[1]).resolve()
    src_dir = path.parent.parent
    sys.path.insert(0, str(src_dir))
    sys.path.insert(0, str(path.parent))

    try:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        module.SkipTest = SkipTest
        spec.loader.exec_module(module)
    except SkipTest as exc:
        print(f"SKIP: {exc}")
        return 0
    except Exception:
        traceback.print_exc()
        return 1

    tests = [getattr(module, name) for name in dir(module)
             if name.startswith("test_") and callable(getattr(module, name))]
    setup = getattr(module, "setup_function", None)
    teardown = getattr(module, "teardown_function", None)
    failed = False
    for test in tests:
        try:
            if setup:
                setup(test)
            test()
            print(f"PASS {path.stem}::{test.__name__}")
        except SkipTest as exc:
            print(f"SKIP {path.stem}::{test.__name__}: {exc}")
        except Exception:
            failed = True
            print(f"FAIL {path.stem}::{test.__name__}")
            traceback.print_exc()
        finally:
            if teardown:
                try:
                    teardown(test)
                except Exception:
                    failed = True
                    traceback.print_exc()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
