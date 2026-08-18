"""Drive the real console in a headless browser against the live test
backend. Catches what a syntax check cannot: runtime errors on render,
broken wiring, views that throw when a list is empty."""
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8099"
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
    pg.fill("input[type=password]", "testpw")
    pg.click("text=Open console")
    pg.wait_for_timeout(1200)
    check(pg.locator("h1:has-text('Overview')").count() > 0, "logs in to Overview")

    print("\n== all nav pages render ==")
    for label, heading in [("Documents", "Documents"), ("Answers", "Answers"),
                           ("Unanswered", "Unanswered"), ("Products", "Products"),
                           ("Widget design", "Widget design"),
                           ("Enquiries", "Enquiries"), ("Test chat", "Test chat")]:
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
    pg.click(".nav button:has-text('Answers')")
    pg.wait_for_timeout(600)
    qbox = pg.locator("input[placeholder*='internet connection']")
    qbox.fill("Is the hopper waterproof?")
    pg.locator("textarea[placeholder*='No. Everything']").fill("No, it is for indoor use only.")
    pg.click("button:has-text('Add answer')")
    pg.wait_for_timeout(1000)
    check(pg.locator("text=Added").count() > 0, "hand-written answer saves")
    pg.wait_for_timeout(400)
    check(pg.locator(".nav button:has-text('Answers') .badge").inner_text().strip() != "0",
          "answer count updates in nav")

    print("\n== unanswered: empty state, then a real gap ==")
    pg.click(".nav button:has-text('Unanswered')")
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
    pg.click(".nav button:has-text('Unanswered')")
    pg.wait_for_timeout(600)
    check(pg.locator("text=Bluetooth pairing").count() > 0, "recorded gap appears")
    check(pg.locator("text=2×").count() > 0, "repeat asks counted, not duplicated")

    print("\n== answer a gap -> prefills and resolves ==")
    pg.click("button:has-text('Answer this')")
    pg.wait_for_timeout(800)
    check(pg.locator("h1:has-text('Answers')").count() > 0, "jumps to Answers")
    val = pg.locator("input[placeholder*='internet connection']").input_value()
    check("Bluetooth" in val, f"question prefilled ({val[:40]!r})")
    pg.locator("textarea[placeholder*='No. Everything']").fill("Yes, pairing is supported over BLE.")
    pg.click("button:has-text('Add answer')")
    pg.wait_for_timeout(1200)
    pg.click(".nav button:has-text('Unanswered')")
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
