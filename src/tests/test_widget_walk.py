"""The widget walked in a real browser against a stubbed backend: ask a
question over /widget/ask/stream (8.11), vote on the answer (8.10), hit the
daily limit, submit the contact form to get a GO- reference (8.9), and see
what the server sends after an answer or a refusal (8.12).

Same set-up as test_widget_phone_and_screen_reader.py: Playwright, every
request answered by _stub, skipped when no Chromium build is installed.
"""
# run-in-own-process: a second sync Playwright driver in the same process as
# test_widget_phone_and_screen_reader.py fails to start.
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
    ("meta", {"sources": [{"source": MANUAL, "page_label": "page 12"}],
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
    host_token[0] = 'data-token="member-token"' if "signed_in" in func.__name__ else ""
    _page = _browser.new_page()
    _page.route("**/*", _stub)
    _page.goto("https://example.test/")
    _page.click(".go-launch")


def teardown_function(func):
    if _page:
        _page.close()


def _ask(q):
    _page.click("button:has-text('Request technical support')")
    _page.wait_for_selector(".go-in:not([disabled])")
    _page.fill(".go-in", q)
    _page.keyboard.press("Enter")


def test_an_answer_arrives_over_the_stream_and_can_be_voted_on():
    _ask("How do I remove the bezel?")
    _page.wait_for_selector(f".go-b.bot:has-text('{ANSWER[:20]}')")
    assert any(u.endswith("/widget/ask/stream") for u, _ in posted), \
        "the widget asks through the stream"
    _page.wait_for_function(
        "[...document.querySelectorAll('.go-b.bot')].some(b => b.textContent.includes('clips first'))")
    down = _page.locator(".go-vote button[aria-label='Not helpful']")
    assert down.count() == 1, "one vote row, under the answer"
    down.click()
    _page.wait_for_function("document.querySelector('.go-vote button').disabled")
    votes = [b for u, b in posted if u.endswith("/widget/feedback")]
    assert votes == [{"request_id": "rid-walk", "vote": "down",
                      "session_id": votes[0]["session_id"],
                      "visitor_id": votes[0]["visitor_id"]}]
    assert down.get_attribute("aria-pressed") == "true"


def test_a_limit_reached_says_so_instead_of_blaming_the_connection():
    stream_body[0] = STREAM_429
    _ask("How do I remove the bezel?")
    _page.wait_for_selector(".go-b.bot:has-text('reached its limit')")
    assert _page.locator("text=check your connection").count() == 0
    assert _page.locator("button:has-text('Start a new chat')").count() == 1


def test_the_contact_form_gives_a_reference():
    _page.click("button:has-text('Speak to sales')")
    _page.click("button:has-text('just my details')")
    _page.wait_for_selector(".go-fbtn")
    inputs = _page.locator(".go-fwrap input")
    inputs.nth(0).fill("Dana Reeves")
    inputs.nth(1).fill("dana@buyer.test")
    _page.click(".go-fbtn")
    _page.wait_for_selector(".go-b.bot:has-text('GO-ABCD1234')")
    lead = next(b for u, b in posted if u.endswith("/widget/lead"))
    assert lead.get("visitor_id"), "the widget sends its visitor id for the daily cap"


SAVED_COUNT = "JSON.parse(localStorage.getItem('groundedops_widget_v2') || '{}').messages.length"


def test_after_an_answer_signed_in_more_detail_the_page_and_related_questions():
    stream_body[0] = STREAM_DETAIL
    _ask("How do I remove the bezel?")
    _page.wait_for_selector("button:has-text('Show more from the manual')")
    assert _page.locator("summary:has-text('Verified against NV9 Manual, page 12')").count() == 1
    assert _page.locator("button:has-text('Open NV9 Manual, page 12')").count() == 1
    assert _page.locator("button:has-text('What voltage does it need?')").count() == 1
    assert _page.locator("button:has-text('How do I clean the sensor?')").count() == 1
    assert _page.locator("button:has-text('How do I remove the bezel?')").count() == 0,         "the question just asked is not offered again"
    n_msgs = _page.evaluate(SAVED_COUNT)
    _page.click("button:has-text('Show more from the manual')")
    _page.wait_for_selector("blockquote.go-quote:has-text('Clip B releases')")
    assert _page.locator("text=From NV9 Manual, page 14").count() == 1
    n_after = _page.evaluate(SAVED_COUNT)
    assert n_after == n_msgs, "the excerpt is not saved as a chat message"


def test_a_related_question_is_asked_by_id():
    stream_body[0] = STREAM_DETAIL
    _ask("How do I remove the bezel?")
    _page.wait_for_selector("button:has-text('What voltage does it need?')")
    _page.click("button:has-text('What voltage does it need?')")
    _page.wait_for_function("document.querySelectorAll('.go-b.usr').length >= 2")
    asks = [b for u, b in posted if u.endswith("/widget/ask/stream")]
    assert asks[-1]["faq_id"] == "f3" and asks[-1]["q"] == "What voltage does it need?"


def test_a_signed_in_refusal_offers_the_closest_page_beside_support():
    stream_body[0] = STREAM_REFUSAL
    _ask("Can it make coffee?")
    _page.wait_for_selector("button:has-text('Email support')")
    assert _page.locator("button:has-text('Open NV9 Manual, page 30')").count() == 1


def test_a_guest_is_not_offered_a_document_it_cannot_open():
    stream_body[0] = STREAM_REFUSAL
    _ask("Can it make coffee?")
    _page.wait_for_selector("button:has-text('Email support')")
    assert _page.locator("button:has-text('Open NV9 Manual')").count() == 0
