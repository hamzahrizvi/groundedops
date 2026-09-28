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

Skips (does not fail) when no Chromium build is installed, the same policy
tests/conftest.py already gives an isolated file with a missing optional
dependency.
"""
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

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


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"no chromium build installed: {e}")
            return
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page()
    pg.route("**/*", _stub)
    pg.goto("https://example.test/")
    yield pg
    pg.close()


def _open(page):
    page.click(".go-launch")
    page.wait_for_selector(".go-panel.go-open, .go-open .go-panel")


def test_opening_the_panel_moves_focus_into_it(page):
    """Bug 1: the launcher used to hold focus, vanish, and drop it."""
    page.click(".go-launch")
    page.wait_for_function(
        "document.querySelector('.go-w').classList.contains('go-open')")
    focused = page.evaluate(
        "document.activeElement && document.activeElement.className")
    assert focused is not None and "go-panel" in focused


def test_the_dialog_announces_itself_as_modal(page):
    _open(page)
    panel = page.query_selector(".go-panel")
    assert panel.get_attribute("role") == "dialog"
    assert panel.get_attribute("aria-modal") == "true"
    assert panel.get_attribute("aria-label")


def test_escape_returns_focus_to_the_launcher(page):
    _open(page)
    page.keyboard.press("Escape")
    page.wait_for_function(
        "!document.querySelector('.go-w').classList.contains('go-open')")
    focused = page.evaluate(
        "document.activeElement && document.activeElement.className")
    assert focused is not None and "go-launch" in focused


def test_phone_viewport_fills_the_screen_edge_to_edge(page):
    """Bug 3: a 375-wide phone used to see a 343x?? card with rounded
    corners floating over the page instead of a full sheet."""
    page.set_viewport_size({"width": 375, "height": 812})
    _open(page)
    box = page.eval_on_selector(".go-panel", """(el) => {
        const r = el.getBoundingClientRect();
        return {w: r.width, h: r.height,
                radius: getComputedStyle(el).borderRadius};
    }""")
    assert box["w"] == 375
    assert box["h"] == 812
    assert box["radius"] in ("0px", "0")


def test_phone_composer_font_size_is_16px_or_more(page):
    """Bug 2: iOS Safari auto-zooms the page on focusing any text input
    under 16px, and stays zoomed in after the field blurs."""
    page.set_viewport_size({"width": 375, "height": 812})
    _open(page)
    size = page.eval_on_selector(
        ".go-in", "(el) => parseFloat(getComputedStyle(el).fontSize)")
    assert size >= 16


def test_phone_header_buttons_meet_the_touch_target_minimum(page):
    page.set_viewport_size({"width": 375, "height": 812})
    _open(page)
    boxes = page.eval_on_selector_all(".go-hbtn", """(els) =>
        els.map(el => {
            const r = el.getBoundingClientRect();
            return {w: r.width, h: r.height};
        })""")
    assert boxes
    for b in boxes:
        assert b["w"] >= 44 and b["h"] >= 44, boxes


def test_desktop_layout_is_unaffected(page):
    """The phone fixes are scoped to the <=480px media query; a normal
    desktop viewport keeps the floating card."""
    page.set_viewport_size({"width": 1280, "height": 900})
    _open(page)
    box = page.eval_on_selector(".go-panel", """(el) => {
        const r = el.getBoundingClientRect();
        return {w: r.width, h: r.height};
    }""")
    assert box["w"] == 400
    assert box["h"] == 600
