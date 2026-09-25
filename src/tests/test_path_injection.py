"""A document name from a request must not select a file by path.

CodeQL alert 123 (py/path-injection) and the four sinks in structures.py
it flows into (160, 176, 179, 494). `_verbatim_faq_pairs` built its path
with `os.path.join(docstore.store_dir(), source)`, and `source` comes
from a request body -- where join() treats an absolute path or a "../"
prefix as an instruction rather than as a filename. The result was handed
to pdfplumber, and whatever it could parse went into drafted FAQ text.

Reproduced before fixing, against a scratch store:

    join("../secret.pdf")               -> exists=True   (outside the store)
    join("/tmp/pathinjection/secret.pdf") -> exists=True (absolute wins)
    find(...) for both                  -> None

docstore.find() is the fix and already existed: it reduces the name to a
bare basename, handling the Windows-backslash case that basename() alone
misses on POSIX, and returns a path built from a matching os.listdir
ENTRY rather than from the caller's string.

These tests are about the BOUNDARY, so they use the real docstore against
a scratch store rather than mocking it -- a mock would agree with whatever
the code does, which is the one thing not worth checking here.
"""
import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import docstore  # noqa: E402

_STORE = "/tmp/pathinjection/store"
_OUTSIDE = "/tmp/pathinjection/secret.pdf"
os.makedirs(_STORE, exist_ok=True)
with open(os.path.join(_STORE, "Real Manual-v1.pdf"), "w", encoding="utf-8") as _f:
    _f.write("a genuine retained original")
with open(_OUTSIDE, "w", encoding="utf-8") as _f:
    _f.write("NOT IN THE STORE")

# The two traversal families, kept as data so each test reads as a list of
# what must be refused rather than as string-escaping.
_RELATIVE = ("../secret.pdf",
             "../../tmp/pathinjection/secret.pdf",
             "subdir/../../secret.pdf")
_WINDOWS = ("..\\secret.pdf",
            "..\\..\\tmp\\pathinjection\\secret.pdf")
_ABSOLUTE = (_OUTSIDE, "/etc/passwd", "C:\\Windows\\win.ini")
_DEGENERATE = ("", ".", "..", "...", "/", "\\")


@contextlib.contextmanager
def store():
    """Point the document store at the scratch dir for ONE test.

    Scoped rather than set at import, deliberately. This file is collected
    in the shared interpreter and run_tests spawns the isolated tests with
    env=dict(os.environ) -- so a SOURCE_FILE_DIR set at import leaks into
    every later test, where _harness sets its own scratch path with
    setdefault and would then decline to override it, pointing the backup
    tests at this directory instead of theirs. store_dir() reads the
    variable on every call, so scoping it costs nothing.
    """
    before = os.environ.get("SOURCE_FILE_DIR")
    os.environ["SOURCE_FILE_DIR"] = _STORE
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("SOURCE_FILE_DIR", None)
        else:
            os.environ["SOURCE_FILE_DIR"] = before


def test_a_real_document_is_still_found():
    """The guard has to let the ordinary case through, or it is just an
    outage with a security rationale."""
    with store():
        found = docstore.find("Real Manual-v1.pdf")
    assert found and os.path.isfile(found)
    assert os.path.basename(found) == "Real Manual-v1.pdf"


def test_a_relative_traversal_is_refused():
    with store():
        for attempt in _RELATIVE:
            assert docstore.find(attempt) is None, attempt


def test_a_windows_style_traversal_is_refused():
    """basename() alone does NOT defeat this on POSIX, where a backslash
    is a legal filename character -- which is why docstore normalises the
    separators before stripping."""
    with store():
        for attempt in _WINDOWS:
            assert docstore.find(attempt) is None, attempt


def test_an_absolute_path_is_refused():
    """The one os.path.join gets most wrong: join(root, "/etc/passwd")
    discards the root entirely and returns "/etc/passwd"."""
    with store():
        for attempt in _ABSOLUTE:
            assert docstore.find(attempt) is None, attempt


def test_a_name_that_is_only_dots_is_refused():
    with store():
        for attempt in _DEGENERATE:
            assert docstore.find(attempt) is None, repr(attempt)


def test_the_old_join_really_did_escape_the_store():
    """The vulnerability, demonstrated rather than asserted about. If this
    ever stops being true, the fix below is guarding nothing and this file
    should be deleted rather than left as reassurance."""
    with store():
        escaped = os.path.join(docstore.store_dir(), "../secret.pdf")
        assert os.path.exists(escaped), "expected the old join to escape"
        assert os.path.abspath(escaped) != os.path.abspath(
            os.path.join(_STORE, "secret.pdf"))


def test_the_boundary_uses_find_rather_than_joining():
    """Pins the call site itself. The vulnerability was not in docstore --
    find() was already correct -- it was one caller building the path
    itself, so the guard that matters is that this caller stops doing so.

    Reads the module as TEXT rather than importing it. Importing main loads
    the real src/.env into os.environ, and this file is collected in the
    shared interpreter, so those values then reach every own-process test
    spawned afterwards (run_tests passes env=dict(os.environ)). Measured,
    with this file present and importing main: test_widget_export failed,
    test_router could not load at all, and the suite read 173/175; without
    it, 178/179. Reading the source gives the same guarantee and imports
    nothing. The caller lives in routes_faq.py since the 2026-09-25 split.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "routes_faq.py").read_text(encoding="utf-8", errors="ignore")
    body = src[src.index("def _verbatim_faq_pairs("):][:2000]
    assert "docstore.find(source)" in body
    assert "os.path.join(docstore.store_dir(), source)" not in body, \
        "the path is being built from request text again"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
