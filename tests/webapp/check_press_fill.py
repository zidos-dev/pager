"""Filled buttons (black/lime): pressing changes their tone by ΔL* ≥ 8 in both themes (Fable re-review Н1:
the shared ink overlay was invisible on ink- and lime-filled buttons). Chromium + WebKit, 390x844, light and dark.

Usage: PORT=19331 python3 check_press_fill.py   (own stand on a temp copy of tests/stand/demo2.json; APP_DIR selects the checkout)
"""
import json, os, subprocess, sys, tempfile, time, urllib.request
from io import BytesIO
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "19331")); URL = f"http://127.0.0.1:{PORT}"
APP = Path(os.environ.get("APP_DIR", ROOT.parent.parent / "assets/webapp"))
DATA = Path(tempfile.mkdtemp()) / "d.json"; DATA.write_text((ROOT.parent / "stand/demo2.json").read_text())
fails = 0


def check(ok, name, detail=""):
    global fails; fails += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)


def lin(c): c /= 255; return c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4
def lstar(c):
    y = .2126 * lin(c[0]) + .7152 * lin(c[1]) + .0722 * lin(c[2]); return 116 * (y ** (1 / 3)) - 16 if y > .008856 else 903.3 * y
def patch(page, x, y):
    im = Image.open(BytesIO(page.screenshot(clip={"x": x, "y": y, "width": 4, "height": 4}))).convert("RGB")
    px = list(im.getdata()); return [sum(p[i] for p in px) / len(px) for i in range(3)]


def press(page, sel, name):
    b = page.locator(sel).first.bounding_box(); cx, cy = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    x, y = b["x"] + 6, cy - 2  # left edge inside the fill, away from the label/glyph
    rest = patch(page, x, y)
    page.mouse.move(cx, cy); page.mouse.down(); page.wait_for_timeout(260)
    down = patch(page, x, y)
    page.mouse.move(2, 2); page.mouse.up(); page.wait_for_timeout(150)
    d = abs(lstar(down) - lstar(rest)); check(d >= 8, f"press tone {name}", f"dL*={d:.1f}")


srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, DATA_FILE=str(DATA), PORT=str(PORT), APP_DIR=str(APP)),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen(URL + "/api/sessions", timeout=.3).close(); break
        except OSError: time.sleep(.1)
    else: sys.exit(f"stand did not start on {PORT}")
    with sync_playwright() as pw:
        for eng in ("chromium", "webkit"):
            b = getattr(pw, eng).launch()
            for scheme in ("light", "dark"):
                tag = f"{eng} {scheme}"
                c = b.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme)
                c.route("https://telegram.org/**", lambda r: r.fulfill(body=""))
                c.route("**/api/send*", lambda r: r.fulfill(status=200, body="{}"))  # pressing must not answer the stand
                p = c.new_page(); p.goto(URL + "/app/"); p.wait_for_selector("#list .card"); p.wait_for_timeout(600)
                press(p, "#new-btn", f"«+» {tag}")
                p.locator("#new-btn").click(); p.wait_for_timeout(700); p.locator("#n-text").fill("synthetic task"); p.wait_for_timeout(200)
                press(p, "#fab", f"«New session» {tag}")
                p.goto(URL + "/app/#s=c3e5a7b9"); p.wait_for_timeout(1500); p.locator("#s-text").fill("x"); p.wait_for_timeout(200)
                press(p, "#s-send", f"send {tag}")
                p.goto(URL + "/app/"); p.wait_for_selector('.card[data-id="a1c3e5f7"]'); p.locator('.card[data-id="a1c3e5f7"]').click(); p.wait_for_timeout(1100)
                p.locator('.lab-ask-btn[data-act="own"]').click(); p.wait_for_timeout(600); p.locator(".lab-ask-own-text").fill("x"); p.wait_for_timeout(200)
                press(p, '.lab-ask-btn[data-act="own-send"]', f"own answer «Send» (lime) {tag}")
                c.close()
            b.close()
finally:
    srv.kill()
print("TOTAL", "PASS (0 failed)" if not fails else f"FAIL ({fails} failed)"); sys.exit(1 if fails else 0)
