"""D-20261109-05: the wait line lists the pages found once a wait passes 4 s,
and drops them with the wait; a quick answer shows none."""
# run-in-own-process: a second sync Playwright driver in the same process as
# test_widget_walk.py fails to start.
import atexit
import json
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
    raise SkipTest("needs playwright")  # noqa: F821

HERE = Path(__file__).resolve().parent
WIDGET_JS = (HERE.parent / "widget" / "groundedops-widget.js").read_text(encoding="utf-8")
HOST_HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<script src="/w.js" data-api="https://example.test" data-title="Support" TOKEN></script>
</body></html>"""
CATALOG = {"categories": [{"key": "val", "name": "Validators", "doc_count": 1,
                           "products": [{"key": "nv9", "name": "NV9", "doc_count": 1}]}]}
ANSWER = "The bezel lifts off. Press the two clips first."


def _sse(*events):
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)


FAQ = {"faq": []}
CHAT_OK = {"session_id": "srv-chat-0001", "delete_token": "tok-0001"}
posted = []
host_token = [""]
chat_reply = [CHAT_OK]
widget_cfg = [{}]


def _stub(route):
    url, req = route.request.url, route.request
    if url.endswith("/w.js"):
        return route.fulfill(status=200, content_type="application/javascript", body=WIDGET_JS)
    if url.rstrip("/").endswith("example.test"):
        return route.fulfill(status=200, content_type="text/html",
                             body=HOST_HTML.replace("TOKEN", host_token[0]))
    if req.method == "POST":
        posted.append((url, json.loads(req.post_data or "{}")))
    if "/widget/faq" in url:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FAQ))
    if "/widget/catalog" in url:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(CATALOG))
    if "/widget/config" in url:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(widget_cfg[0]))
    if url.endswith("/widget/chat"):
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(chat_reply[0]))
    if url.endswith("/widget/lead"):
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"received": True, "id": "abcd1234-0000", "reference": "GO-ABCD1234"}))
    route.fulfill(status=200, content_type="application/json", body="{}")


_pw = sync_playwright().start()
try:
    _browser = _pw.chromium.launch()
except Exception as e:
    _pw.stop()
    raise SkipTest(f"no chromium build installed: {e}")  # noqa: F821


def _shutdown():
    for f in (_browser.close, _pw.stop):
        try:
            f()
        except Exception:
            pass


atexit.register(_shutdown)
_page = None


def setup_function(func):
    global _page
    posted.clear()
    widget_cfg[0] = {}
    chat_reply[0] = CHAT_OK
    host_token[0] = 'data-token="member-token"' if "signed_in" in func.__name__ else ""
    _page = _browser.new_page()
    _page.route("**/*", _stub)
    _page.add_init_script(HOLD_JS)
    _page.goto("https://example.test/")
    _page.click(".go-launch")


def teardown_function(func):
    if _page:
        _page.close()


HEAD = _sse(
    ("status", {"stage": "Searching the documentation", "id": "entry"}),
    ("status", {"stage": "Writing the answer", "id": "procedures",
                "found": [{"source": "NV9_Spectral-manual.pdf", "pages": [14, 15, 41]}]}))
TAIL = _sse(
    ("meta", {"sources": [], "request_id": "rid-f", "role": "fast", "from_faq": False, "flagged": False}),
    ("delta", {"text": ANSWER}),
    ("done", {"flagged": False}))
# The stream is held open after HEAD until window.__release() sends TAIL.
HOLD_JS = """(function () {
  var orig = window.fetch.bind(window);
  window.fetch = function (u, o) {
    if (String(u).endsWith("/widget/ask/stream")) {
      return Promise.resolve(new Response(new ReadableStream({start: function (c) {
        var e = new TextEncoder();
        c.enqueue(e.encode(window.__head));
        window.__release = function () { c.enqueue(e.encode(window.__tail)); c.close(); };
      }}), {status: 200, headers: {"Content-Type": "text/event-stream"}}));
    }
    return orig(u, o);
  };
})();"""


def _send_held():
    _page.evaluate("([h, t]) => { window.__head = h; window.__tail = t; }", [HEAD, TAIL])
    _page.wait_for_selector(".go-in:not([disabled])")
    _page.fill(".go-in", "How do I remove the bezel?")
    _page.keyboard.press("Enter")


def _release_and_wait():
    _page.evaluate("window.__release()")
    _page.wait_for_selector(f".go-b.bot:has-text('{ANSWER[:20]}')")


def test_a_long_wait_shows_the_pages_then_drops_them():
    _send_held()
    _page.wait_for_selector(".go-found:has-text('NV9 Spectral manual p. 14, 15, 41')", timeout=8000)
    _release_and_wait()
    assert _page.locator(".go-found").count() == 0


def test_a_quick_answer_shows_no_pages():
    _send_held()
    _page.wait_for_timeout(1500)
    assert _page.locator(".go-found").count() == 0
    _release_and_wait()
    _page.wait_for_timeout(3000)
    assert _page.locator(".go-found").count() == 0
