"""8.13: phone layout and screen-reader fixes, walked through in a real browser.

Playwright drives an actual Chromium page loading groundedops-widget.js with
no backend running -- /widget/config, /widget/catalog and /widget/quota are
stubbed (the widget already fails open on any of them, by design: see its
own .catch() handlers), since this step is about the panel's own layout and
focus handling, not the pipeline behind it.

Three bugs found by walking through the panel as a phone / a screen reader
would, all fixed in groundedops-widget.js alongside this test:

  1. Opening the panel hid the launcher button (display:none) without moving
     focus anywhere, so a keyboard or screen-reader user who activated it
     landed on <body> with no sense of where they were.
  2. The composer textarea's 14px font-size is below the 16px Mobile Safari
     treats as "the user won't need to zoom to read this", so focusing it
     zoomed the whole page in and left it that way after blur.
  3. Below 480px the panel stayed a 400x600 floating card pinned to a
     corner, leaving barely any margin on a real phone screen instead of
     using it as a native sheet would.

No pytest fixtures: run_tests.py's own discovery calls every module-level
test_* function with no arguments (see its discover_and_run), the same
constraint test_ui.py works under. setup_function/teardown_function give a
fresh page per test instead -- xunit-style hooks pytest also runs natively,
so this file works the same way under both runners.

Skips (does not fail) when no Chromium build is installed, the same policy
tests/conftest.py already gives an isolated file with a missing optional
dependency.
"""
import atexit
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
    raise SkipTest("needs playwright")  # noqa: F821

HERE = Path(__file__).resolve().parent
WIDGET_JS = (HERE.parent / "widget" / "groundedops-widget.js").read_text(encoding="utf-8")

HOST_HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<script src="/w.js" data-api="https://example.test" data-title="Support"
        data-agent-name="Assistant"></script>
</body></html>"""


def _stub(route):
    url = route.request.url
    if url.endswith("/w.js"):
        route.fulfill(status=200, content_type="application/javascript",
                      body=WIDGET_JS)
        return
    if url.rstrip("/").endswith("example.test"):
        route.fulfill(status=200, content_type="text/html", body=HOST_HTML)
        return
    # Every other call (/widget/config, /widget/catalog, /widget/quota, ...)
    # -- the widget's own fetch handlers fail open on an empty/odd body, see
    # applyServerConfig, fetchCatalog and refreshQuota's .catch().
    route.fulfill(status=200, content_type="application/json", body="{}")


_pw = sync_playwright().start()
try:
    _browser = _pw.chromium.launch()
except Exception as e:
    _pw.stop()
    raise SkipTest(f"no chromium build installed: {e}")  # noqa: F821


def _shutdown():
    # Order matters (browser before driver) and interpreter shutdown can
    # already have torn down the event loop by the time this runs, which
    # is cosmetic -- the process is exiting either way.
    try:
        _browser.close()
    except Exception:
        pass
    try:
        _pw.stop()
    except Exception:
        pass


atexit.register(_shutdown)

_page = None


def setup_function(func):
    global _page
    _page = _browser.new_page()
    _page.route("**/*", _stub)
    _page.goto("https://example.test/")


def teardown_function(func):
    if _page:
        _page.close()


def _open():
    _page.click(".go-launch")
    _page.wait_for_selector(".go-panel.go-open, .go-open .go-panel")


def test_opening_the_panel_moves_focus_into_it():
    """Bug 1: the launcher used to hold focus, vanish, and drop it."""
    _page.click(".go-launch")
    _page.wait_for_function(
        "document.querySelector('.go-w').classList.contains('go-open')")
    focused = _page.evaluate(
        "document.activeElement && document.activeElement.className")
    assert focused is not None and "go-panel" in focused


def test_the_dialog_announces_itself_as_modal():
    _open()
    panel = _page.query_selector(".go-panel")
    assert panel.get_attribute("role") == "dialog"
    assert panel.get_attribute("aria-modal") == "true"
    assert panel.get_attribute("aria-label")


def test_escape_returns_focus_to_the_launcher():
    _open()
    _page.keyboard.press("Escape")
    _page.wait_for_function(
        "!document.querySelector('.go-w').classList.contains('go-open')")
    focused = _page.evaluate(
        "document.activeElement && document.activeElement.className")
    assert focused is not None and "go-launch" in focused


def test_phone_viewport_fills_the_screen_edge_to_edge():
    """Bug 3: a 375-wide phone used to see a 343x?? card with rounded
    corners floating over the page instead of a full sheet."""
    _page.set_viewport_size({"width": 375, "height": 812})
    _open()
    box = _page.eval_on_selector(".go-panel", """(el) => {
        const r = el.getBoundingClientRect();
        return {w: r.width, h: r.height,
                radius: getComputedStyle(el).borderRadius};
    }""")
    assert box["w"] == 375
    assert box["h"] == 812
    assert box["radius"] in ("0px", "0")


def test_phone_composer_font_size_is_16px_or_more():
    """Bug 2: iOS Safari auto-zooms the page on focusing any text input
    under 16px, and stays zoomed in after the field blurs."""
    _page.set_viewport_size({"width": 375, "height": 812})
    _open()
    size = _page.eval_on_selector(
        ".go-in", "(el) => parseFloat(getComputedStyle(el).fontSize)")
    assert size >= 16


def test_phone_header_buttons_meet_the_touch_target_minimum():
    _page.set_viewport_size({"width": 375, "height": 812})
    _open()
    boxes = _page.eval_on_selector_all(".go-hbtn", """(els) =>
        els.map(el => {
            const r = el.getBoundingClientRect();
            return {w: r.width, h: r.height};
        })""")
    assert boxes
    for b in boxes:
        assert b["w"] >= 44 and b["h"] >= 44, boxes


def test_desktop_layout_is_unaffected():
    """The phone fixes are scoped to the <=480px media query; a normal
    desktop viewport keeps the floating card."""
    _page.set_viewport_size({"width": 1280, "height": 900})
    _open()
    box = _page.eval_on_selector(".go-panel", """(el) => {
        const r = el.getBoundingClientRect();
        return {w: r.width, h: r.height};
    }""")
    assert box["w"] == 400
    assert box["h"] == 600
