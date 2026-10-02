"""Capture every page in both themes so light mode can be audited, not assumed."""
import os
from playwright.sync_api import sync_playwright
BASE="http://127.0.0.1:8099"; OUT="C:/tmp/theme"
PAGES=[("Overview","overview"),("Documents","documents"),
       ("Generate/Edit FAQs","faq"),("FAQs from customers","gaps"),
       ("Products","catalog"),("Widget design","widget"),("Enquiries","leads"),
       ("Accounts","accounts"),("Access & limits","policy"),("Advanced","advanced")]
with sync_playwright() as pw:
    br=pw.chromium.launch()
    for theme in ("dark","light"):
        os.makedirs(f"{OUT}/{theme}", exist_ok=True)
        pg=br.new_page(viewport={"width":1500,"height":950})
        errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE+"/admin"); pg.wait_for_timeout(2200)
        pg.evaluate("t => { localStorage.setItem('go.theme', t); }", theme)
        pg.reload(); pg.wait_for_timeout(2000)
        pg.fill("input[type=email]","design@example.com")
        pg.fill("input[type=password]","design-audit-password-123")
        n=pg.locator("input[placeholder*='Your name']")
        if n.count() and n.first.is_visible(): n.first.fill("Design Audit")
        # .btn-primary, not the first button in the card: the sign-in page
        # now carries a theme toggle, which comes first in the DOM.
        pg.locator(".gate .btn-primary").first.click(); pg.wait_for_timeout(2500)
        applied=pg.evaluate("()=>document.documentElement.dataset.theme")
        bg=pg.evaluate("()=>getComputedStyle(document.body).backgroundColor")
        print("%-6s applied=%-6s body bg=%s  jsErr=%d" % (theme, applied, bg, len(errs)))
        for label,slug in PAGES:
            try:
                pg.click(f".nav button:has-text('{label}')", timeout=7000)
                pg.wait_for_timeout(800)
                pg.screenshot(path=f"{OUT}/{theme}/{slug}.png", full_page=True)
            except Exception as e: print("   miss", slug, str(e)[:50])
        pg.close()
    br.close()
