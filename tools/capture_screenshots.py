"""Capture README screenshots + an animated GIF of the widget flow.

Runs the real widget against the real backend, so every answer and citation
in the docs is one the system actually produced.
"""
import os, sys, time
from playwright.sync_api import sync_playwright

DEMO = "http://127.0.0.1:5599/demo.html"
OUT  = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "img")
OUT  = sys.argv[1] if len(sys.argv) > 1 else "docs/img"
os.makedirs(OUT, exist_ok=True)
W, H = 1180, 900
frames = []

def panel_clip(page, pad=26):
    r = page.evaluate("""() => {
      const n = document.querySelector('.go-panel, #go-panel')
             || [...document.querySelectorAll('div')].find(d =>
                  d.className && String(d.className).includes('go-') &&
                  d.getBoundingClientRect().height > 300);
      if (!n) return null; const b = n.getBoundingClientRect();
      return {x:b.x, y:b.y, width:b.width, height:b.height}; }""")
    if not r: return None
    x = max(0, r["x"]-pad); y = max(0, r["y"]-pad)
    return {"x": x, "y": y,
            "width": min(r["width"]+pad*2, 1180-x),
            "height": min(r["height"]+pad*2, 900-y)}


def shot(page, name, clip="panel"):
    p = os.path.join(OUT, name)
    c = panel_clip(page) if clip == "panel" else clip
    page.screenshot(path=p, clip=c)
    print("  wrote", p)

def widget_box(page):
    b = page.evaluate("""() => {
      const r = document.querySelector('#go-panel, .go-panel, [class*=go-]')
        ?.getBoundingClientRect();
      return r ? {x:r.x, y:r.y, width:r.width, height:r.height} : null; }""")
    return b

with sync_playwright() as pw:
    br = pw.chromium.launch()
    pg = br.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
    pg.goto(DEMO, wait_until="networkidle")
    pg.wait_for_timeout(1500)

    # 1. closed launcher on the host page
    shot(pg, "01-launcher.png", clip=None)

    pg.get_by_text("Ask a question").click()
    pg.wait_for_timeout(2500)
    shot(pg, "02-opening.png")
    frames.append(pg.screenshot())

    pg.get_by_role("button", name="Ask about a product").click()
    pg.wait_for_timeout(2000)
    shot(pg, "03-ranges.png")
    frames.append(pg.screenshot())

    pg.get_by_role("button", name="Note Validator").click()
    pg.wait_for_timeout(2500)
    shot(pg, "04-products.png")
    frames.append(pg.screenshot())

    for nm in ("NV9 Spectral", "NV9USB+"):
        try:
            pg.get_by_role("button", name=nm).first.click(timeout=3000); break
        except Exception:
            continue
    pg.wait_for_timeout(2500)
    shot(pg, "05-faq-suggestions.png")
    frames.append(pg.screenshot())

    box = pg.locator("input[placeholder*='Ask about'], textarea[placeholder*='Ask about']").first
    box.click()
    box.type("My bezel is flashing one long flash then one short flash - what's wrong?", delay=18)
    frames.append(pg.screenshot())
    pg.keyboard.press("Enter")
    try:
        pg.locator("text=SOURCES").first.wait_for(timeout=90000)
    except Exception:
        pg.wait_for_timeout(30000)
    pg.wait_for_timeout(1200)
    shot(pg, "06-grounded-answer.png")
    frames.append(pg.screenshot()); frames.append(pg.screenshot())

    try:
        pg.locator("text=SOURCES").first.click()
        pg.wait_for_timeout(700)
        # the log scrolls independently; pin it to the bottom so the source
        # line is not clipped by the composer
        pg.evaluate("""() => {
          const l = [...document.querySelectorAll('div')].find(d =>
            d.scrollHeight > d.clientHeight + 20 && d.clientHeight > 150);
          if (l) l.scrollTop = l.scrollHeight; }""")
        pg.wait_for_timeout(600)
        shot(pg, "07-sources-expanded.png")
        frames.append(pg.screenshot()); frames.append(pg.screenshot())
    except Exception as e:
        print("  sources expand skipped:", e)

    br.close()

# assemble the GIF
try:
    from PIL import Image
    import io
    imgs = [Image.open(io.BytesIO(f)).convert("P", palette=Image.ADAPTIVE)
            for f in frames]
    gif = os.path.join(OUT, "widget-demo.gif")
    imgs[0].save(gif, save_all=True, append_images=imgs[1:],
                 duration=1600, loop=0, optimize=True)
    print("  wrote", gif, "(%d frames)" % len(imgs))
except Exception as e:
    print("  GIF skipped:", e)
