"""The "Search more thoroughly" button after a refusal (D-20261026-93).

A signed-in member with enough credits gets one deep re-ask of the same
question; guests, short balances, handoffs, answers and a refusal that already
came from a deep ask get none. Same set-up as test_widget_walk.py.
"""
# run-in-own-process: a second sync Playwright driver in the same process as
# test_widget_phone_and_screen_reader.py fails to start.
import atexit
import json
import os
import tempfile
from pathlib import Path

import quota

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
MEMBER = {"tier": "member", "mode": "full", "limit": 40, "used": 20, "remaining": 20,
          "unit": "credits", "reset_at": 0, "ai_available": True, "deep_available": True,
          "standard_cost": 1, "deep_cost": 4}
GUEST = {**MEMBER, "tier": "anonymous", "deep_available": False}


def _sse(*events):
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)


def _refusal(quota, role="rejected"):
    return _sse(("meta", {"sources": [], "request_id": "rid-r", "role": role,
                          "offer_support": True, "quota": quota}),
                ("delta", {"text": "I could not find that in the documentation."}))


def _answer(quota):
    return _sse(("meta", {"sources": [], "request_id": "rid-a", "role": "fast", "quota": quota}),
                ("delta", {"text": "The bezel lifts off."}))


DEEP = "button:has-text('Search more thoroughly')"
EMAIL = "button:has-text('Email support')"


def asks():
    return [b for u, b in posted if u.endswith("/widget/ask/stream")]


posted = []
stream_body = [""]
host_token = [""]
quota_body = [{}]


def _stub(route):
    url, req = route.request.url, route.request
    if url.endswith("/w.js"):
        return route.fulfill(status=200, content_type="application/javascript", body=WIDGET_JS)
    if url.rstrip("/").endswith("example.test"):
        return route.fulfill(status=200, content_type="text/html",
                             body=HOST_HTML.replace("TOKEN", host_token[0]))
    if req.method == "POST":
        posted.append((url, json.loads(req.post_data or "{}")))
    if "/widget/catalog" in url:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(CATALOG))
    if url.endswith("/widget/ask/stream"):
        return route.fulfill(status=200, content_type="text/event-stream", body=stream_body[0])
    if "/widget/quota" in url:
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(quota_body[0]))
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
    signed = "signed_in" in func.__name__
    stream_body[0] = ""
    quota_body[0] = MEMBER if signed else GUEST
    host_token[0] = 'data-token="member-token"' if signed else ""
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


def test_signed_in_refusal_offers_a_deep_search_once():
    stream_body[0] = _refusal(MEMBER)
    _ask("Can it make coffee?")
    _page.wait_for_selector(EMAIL)
    assert _page.locator(DEEP).count() == 1
    assert _page.locator("text=Uses 4 of your 20 credits today (a normal answer uses 1)").count() == 1
    stream_body[0] = _refusal({**MEMBER, "used": 24, "remaining": 16})
    _page.click(DEEP)
    _page.wait_for_function("document.querySelectorAll('.go-b.usr').length >= 2")
    _page.wait_for_selector(EMAIL)
    a = asks()
    assert len(a) == 2
    assert a[0]["effort"] == "standard" and a[1]["effort"] == "deep"
    assert a[1]["q"] == a[0]["q"]
    assert a[1]["session_id"] == a[0]["session_id"]
    assert a[1]["skip_faq"] is False
    assert _page.locator(DEEP).count() == 0, "not offered after the deep turn"


def test_a_guest_refusal_has_no_deep_search():
    stream_body[0] = _refusal(GUEST)
    _ask("Can it make coffee?")
    _page.wait_for_selector(EMAIL)
    assert _page.locator(DEEP).count() == 0


def test_signed_in_short_of_credits_has_no_deep_search():
    stream_body[0] = _refusal({**MEMBER, "remaining": 3})
    _ask("Can it make coffee?")
    _page.wait_for_selector(EMAIL)
    assert _page.locator(DEEP).count() == 0


def test_signed_in_answer_has_no_deep_search():
    stream_body[0] = _answer(MEMBER)
    _ask("How do I remove the bezel?")
    _page.wait_for_selector(".go-b.bot:has-text('bezel lifts off')")
    assert _page.locator(DEEP).count() == 0


def test_signed_in_handoff_has_no_deep_search():
    stream_body[0] = _refusal(MEMBER, role="handoff")
    _ask("Can it make coffee?")
    _page.wait_for_selector(EMAIL)
    assert _page.locator(DEEP).count() == 0


def test_deep_is_charged_deep_cost():
    saved = quota.DB_PATH
    quota.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="quota-deep-"), "q.db")
    quota.init_db()
    try:
        c = {"tier": "member", "identity": "u:deeptest", "ip_identity": None, "uid": "deeptest"}
        level, spec = quota.resolve_effort("deep", "member")
        assert level == "deep"
        before = quota.status(c)
        after = quota.consume(c, spec["credits"])
        assert before["remaining"] - after["remaining"] == before["deep_cost"]
        assert quota.resolve_effort("deep", "anonymous")[0] != "deep"
    finally:
        quota.DB_PATH = saved
