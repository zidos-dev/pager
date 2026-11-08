"""Letters keep the font's own widths (dogfood 08.11: Chromium on Linux hints Onest «т» from 6.97 to 9 px at 15 px,
so «Активные», «Остановить», «лимитами» read as «Акт ивные», «Ост ановить», «лимит ами»). Ten «т» at 15 px in the
app's font must measure within 10 % (WebKit snaps to half pixels) of the font's advance (465/1000 em at weight 400). Text, buttons and text fields; Chromium and WebKit, device scale 2.

Usage: python3 check_glyph_width.py   (APP_DIR selects the checkout)
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[2])) / "assets/webapp/index.html"
WANT = 10 * 465 / 1000 * 15
MEASURE = """async(tag)=>{await document.fonts.load('400 15px Onest','т');const s=document.createElement(tag);
 s.style.cssText='font-size:15px;font-weight:400;position:absolute;white-space:pre;padding:0;border:0;font-family:var(--font)';s.textContent='тттттттттт';
 document.body.append(s);return s.getBoundingClientRect().width}"""

bad = []
with sync_playwright() as p:
    for name in ("chromium", "webkit"):
        b = getattr(p, name).launch()
        pg = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        pg.route("https://telegram.org/**", lambda r: r.fulfill(status=200, content_type="text/javascript", body=""))
        pg.goto(APP.as_uri())
        for tag in ("span", "button", "textarea"):
            w = pg.evaluate(MEASURE, tag) if tag != "textarea" else pg.evaluate(MEASURE.replace("s.textContent", "s.value").replace("getBoundingClientRect().width", "scrollWidth").replace("white-space:pre", "white-space:pre;width:1px"), tag)
            ok = abs(w - WANT) / WANT < 0.10
            print(f"{name} {tag}: 10×т = {w:.1f}px, font says {WANT:.1f}px", "OK" if ok else "FAIL")
            ok or bad.append(f"{name} {tag}")
        b.close()
print("PASS" if not bad else "FAIL " + ",".join(bad))
sys.exit(1 if bad else 0)
