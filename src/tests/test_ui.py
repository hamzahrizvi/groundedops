"""Drive the real console in a headless browser against the live test
backend. Catches what a syntax check cannot: runtime errors on render,
broken wiring, views that throw when a list is empty.

HOW TO RUN. It needs a backend on 127.0.0.1:8099 that the harness does not
start, and a FRESH one each time -- the test answers a gap and marks an
enquiry handled, so a second run against the same instance finds the work
already done and fails on state, not on code. Point the store paths at a
throwaway directory so it never touches a real install:

    T=/tmp/gotest8099 && rm -rf $T && mkdir -p $T/docs $T/src_files
    ACCOUNTS_PATH=$T/accounts.json FAQ_STORE_PATH=$T/faq.json     FAQ_GAP_PATH=$T/gaps.json WIDGET_CONFIG_PATH=$T/widget.json     WIDGET_LEADS_PATH=$T/leads.json CONVO_DB_PATH=$T/convo.db     QUOTA_DB_PATH=$T/quota.db POLICY_PATH=$T/policy.json     CHROMA_DIR=$T/chroma CATALOG_CONFIG=$T/catalog.json     SOURCE_FILE_DIR=$T/src_files ALLOWED_EMAIL_DOMAIN=       python -m uvicorn main:app --host 127.0.0.1 --port 8099

    python tests/test_ui.py

ALLOWED_EMAIL_DOMAIN must be cleared, or the bootstrap below is rejected for
using an address outside the configured domain.
"""
import os
import socket

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8099"

# The console stopped taking a single shared password in v14 (accounts.py):
# it is an email and a password now, against a real account. On an install
# with NO accounts yet the same form bootstraps the first root account, which
# is what a throwaway test instance is, so these defaults just work against a
# fresh one. Point them at a real account to run against something else.
EMAIL = os.getenv("GO_TEST_EMAIL", "ui-test@example.com")
PASSWORD = os.getenv("GO_TEST_PASSWORD", "ui-test-password-123")

# This drives a REAL console against a REAL backend, which the harness does
# not start. Skip rather than fail when nothing is listening: the alternative
# is a red suite on every machine that has playwright installed but no test
# server up, which says nothing about the code under test.
_probe = socket.socket()
_probe.settimeout(1.0)
try:
    _probe.connect(("127.0.0.1", 8099))
except OSError:
    raise SkipTest("needs the test backend on 127.0.0.1:8099")  # noqa: F821
finally:
    _probe.close()
errors, fails = [], []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        fails.append(label)


with sync_playwright() as p:
    br = p.chromium.launch()
    pg = br.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append("console." + m.type + ": " + m.text)
          if m.type == "error" else None)

    pg.goto(BASE + "/admin")
    pg.wait_for_timeout(600)

    print("\n== login gate ==")
    check(pg.locator("text=GroundedOps console").count() > 0, "gate renders")
    pg.fill("input[type=email]", EMAIL)
    pg.fill("input[type=password]", PASSWORD)
    # Shown only when the install has no accounts yet, i.e. this sign-in is
    # really the root bootstrap. Filling it when hidden would throw.
    name = pg.locator("input[placeholder*='Your name']")
    if name.count() and name.first.is_visible():
        name.first.fill("UI Test")
    # The gate has exactly one button, and its label depends on the state:
    # "Sign in" normally, "Create root account" on an install with no
    # accounts. Matching the button rather than the words survives both.
    pg.locator(".gate button").first.click()
    pg.wait_for_timeout(1500)
    check(pg.locator("h1:has-text('Overview')").count() > 0, "logs in to Overview")

    print("\n== all nav pages render ==")
    # Two of these were renamed in the console: "Answers" -> "Generate/Edit
    # FAQs" and "Unanswered" -> "FAQs from customers". The nav label and the
    # h1 are the same string in every case, which is why one list drives both.
    for label, heading in [("Documents", "Documents"),
                           ("Generate/Edit FAQs", "Generate/Edit FAQs"),
                           ("FAQs from customers", "FAQs from customers"),
                           ("Products", "Products"),
                           ("Widget design", "Widget design"),
                           ("Enquiries", "Enquiries"), ("Test chat", "Test chat"),
                           # Root-only pages. The bootstrap account IS root,
                           # so they are reachable here -- and they are the
                           # ones most likely to throw on an empty install,
                           # because they render from their own caches rather
                           # than the shared one.
                           ("Accounts", "Accounts"), ("API keys", "API keys"),
                           ("Access & limits", "Access & limits"),
                           ("Advanced", "Advanced"), ("Backup", "Backup")]:
        pg.click(f".nav button:has-text('{label}')")
        pg.wait_for_timeout(500)
        check(pg.locator(f"h1:has-text('{heading}')").count() > 0, f"{label} renders")

    print("\n== widget design: live preview + save ==")
    pg.click(".nav button:has-text('Widget design')")
    pg.wait_for_timeout(600)
    name_in = pg.locator("input.input").first
    name_in.fill("Acme Helper")
    pg.wait_for_timeout(300)
    check(pg.locator("text=Acme Helper").count() >= 1, "preview updates live as you type")

    # add an opening option, confirm it appears in the preview
    before = pg.locator("button:has-text('Remove')").count()
    pg.click("button:has-text('Add option')")
    pg.wait_for_timeout(300)
    check(pg.locator("button:has-text('Remove')").count() > before, "can add an opening option")

    pg.click("button:has-text('Save changes')")
    pg.wait_for_timeout(900)
    check(pg.locator("text=Saved").count() > 0, "save succeeds")

    # reload and confirm it persisted
    pg.reload()
    pg.wait_for_timeout(1500)
    pg.click(".nav button:has-text('Widget design')")
    pg.wait_for_timeout(700)
    check(pg.locator("input[value='Acme Helper']").count() > 0
          or pg.locator("text=Acme Helper").count() > 0, "config persisted across reload")

    print("\n== answers: write one by hand ==")
    pg.click(".nav button:has-text('Generate/Edit FAQs')")
    pg.wait_for_timeout(600)
    qbox = pg.locator("input[placeholder*='internet connection']")
    qbox.fill("Is the hopper waterproof?")
    pg.locator("textarea[placeholder*='No. Everything']").fill("No, it is for indoor use only.")
    pg.click("button:has-text('Add answer')")
    pg.wait_for_timeout(1000)
    check(pg.locator("text=Added").count() > 0, "hand-written answer saves")
    pg.wait_for_timeout(400)
    check(pg.locator(".nav button:has-text('Generate/Edit FAQs') .badge").inner_text().strip() != "0",
          "answer count updates in nav")

    print("\n== unanswered: empty state, then a real gap ==")
    pg.click(".nav button:has-text('FAQs from customers')")
    pg.wait_for_timeout(500)
    check(pg.locator("text=Nothing unanswered").count() > 0, "empty state renders (no crash)")

    # generate a real miss through the widget pipeline
    import urllib.request, json
    for _ in range(2):
        req = urllib.request.Request(
            BASE + "/query", method="POST",
            data=json.dumps({"q": "Does the hopper support Bluetooth pairing?",
                             "session_id": "ui", "product": "mycheckr"}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req).read()

    pg.reload(); pg.wait_for_timeout(1500)
    pg.click(".nav button:has-text('FAQs from customers')")
    pg.wait_for_timeout(600)
    check(pg.locator("text=Bluetooth pairing").count() > 0, "recorded gap appears")
    check(pg.locator("text=2×").count() > 0, "repeat asks counted, not duplicated")

    print("\n== answer a gap -> prefills and resolves ==")
    pg.click("button:has-text('Answer this')")
    pg.wait_for_timeout(800)
    check(pg.locator("h1:has-text('Generate/Edit FAQs')").count() > 0,
          "jumps to the FAQ editor")
    val = pg.locator("input[placeholder*='internet connection']").input_value()
    check("Bluetooth" in val, f"question prefilled ({val[:40]!r})")
    pg.locator("textarea[placeholder*='No. Everything']").fill("Yes, pairing is supported over BLE.")
    pg.click("button:has-text('Add answer')")
    pg.wait_for_timeout(1200)
    pg.click(".nav button:has-text('FAQs from customers')")
    pg.wait_for_timeout(600)
    check(pg.locator("text=Nothing unanswered").count() > 0,
          "answering the gap auto-resolved it")

    print("\n== enquiries ==")
    req = urllib.request.Request(
        BASE + "/widget/lead", method="POST",
        data=json.dumps({"kind": "sales", "product": "mycheckr",
                         "values": {"f_name": "Dana Reeves", "f_email": "dana@buyer.test",
                                    "f_msg": "Pricing for 50 units?"}}).encode(),
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req).read()
    pg.reload(); pg.wait_for_timeout(1500)
    pg.click(".nav button:has-text('Enquiries')")
    pg.wait_for_timeout(700)
    check(pg.locator("text=Dana Reeves").count() > 0, "submitted enquiry appears")
    check(pg.locator("text=Pricing for 50 units?").count() > 0, "long-answer field shown")
    pg.click("button:has-text('Mark handled')")
    pg.wait_for_timeout(900)
    check(pg.locator("button:has-text('Mark unhandled')").count() > 0, "can mark handled")

    br.close()

print("\n" + "=" * 54)
real = [e for e in errors if "favicon" not in e.lower()]
if real:
    print(f"{len(real)} JS ERROR(S):")
    for e in real[:10]:
        print("  -", e[:160])
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
if not real and not fails:
    print("ALL UI CHECKS PASSED — no JS errors")
raise SystemExit(1 if (real or fails) else 0)
