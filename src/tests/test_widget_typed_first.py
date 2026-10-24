"""A visitor can type the first question without picking a product: the
composer is open on the first screen and the POST carries product=None; after
the buttons are used it carries the chosen product.
"""
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


STREAM_OK = _sse(
    ("status", {"stage": "Searching the documentation", "id": "entry"}),
    ("status", {"stage": "Writing the answer", "id": "procedures"}),
    ("meta", {"sources": [], "request_id": "rid-walk", "role": "fast",
              "from_faq": False, "flagged": False}),
    ("delta", {"text": "The bezel lifts off. "}),
    ("delta", {"text": "Press the two clips first."}),
    ("done", {"flagged": False}))
MANUAL = "NV9 Manual.pdf"
STREAM_DETAIL = _sse(
    ("meta", {"sources": [{"source": MANUAL, "page_label": "page 12",
                           "download_url": "/source_file/x", "pages": [12]}],
              "request_id": "rid-mc", "role": "fast",
              "more_context": {"kind": "detail", "label": "x", "support": False,
                               "document": {"source": MANUAL, "download_url": "/source_file/x",
                                            "pages": [12]},
                               "passages": [{"source": MANUAL, "page": 14,
                                             "text": "Clip B releases the lower bezel."}]}}),
    ("delta", {"text": ANSWER}))
STREAM_REFUSAL = _sse(
    ("meta", {"sources": [], "request_id": "rid-no", "role": "rejected",
              "offer_support": True,
              "more_context": {"kind": "document", "label": "x", "support": True,
                               "document": {"source": MANUAL, "download_url": "/source_file/x",
                                            "pages": [30]},
                               "passages": []}}),
    ("delta", {"text": "I could not find that in the documentation."}))
FAQ = {"faq": [
    {"id": "f1", "question": "How do I remove the bezel?", "answer": "a", "edited": True},
    {"id": "f2", "question": "How do I clean the sensor?", "answer": "a", "edited": False},
    {"id": "f3", "question": "What voltage does it need?", "answer": "a", "edited": True}]}
STREAM_429 = _sse(
    ("status", {"stage": "Searching the documentation", "id": "entry"}),
    ("error", {"status": 429, "detail": {
        "error": "quota_exceeded", "reason": "session_questions",
        "message": "This conversation has reached its limit."}}))

posted = []
stream_body = [STREAM_OK]
host_token = [""]
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
    if url.endswith("/widget/ask/stream"):
        return route.fulfill(status=200, content_type="text/event-stream", body=stream_body[0])
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
    stream_body[0] = STREAM_OK
    widget_cfg[0] = {}
    host_token[0] = 'data-token="member-token"' if "signed_in" in func.__name__ else ""
    _page = _browser.new_page()
    _page.route("**/*", _stub)
    _page.goto("https://example.test/")
    _page.click(".go-launch")


def teardown_function(func):
    if _page:
        _page.close()


def _send(q):
    _page.wait_for_selector(".go-in:not([disabled])")
    _page.fill(".go-in", q)
    _page.keyboard.press("Enter")
    _page.wait_for_selector(f".go-b.bot:has-text('{ANSWER[:20]}')")
    return [b for u, b in posted if u.endswith("/widget/ask/stream")][-1]


def test_typed_first_message_goes_unscoped():
    assert _page.is_enabled(".go-in"), "composer is open on the first screen"
    body = _send("How do I remove the bezel?")
    assert body["product"] is None and body.get("category") is None, body


def test_buttons_still_scope_the_question():
    _page.click("button:has-text('Request technical support')")
    # The composer is open at once now, so wait for the product to be set.
    _page.wait_for_selector(".go-scope:has-text('NV9')")
    body = _send("How do I remove the bezel?")
    assert body["product"] == "nv9", body
