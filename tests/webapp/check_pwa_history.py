"""iPhone home-screen PWA: opening a layer puts nothing into browser history, so the iOS edge swipe has no page to go
«back» to — it reaches the app and the layers morph follows the finger (owner 20.10 10:41: «pwa вне тг живет»; the swipe
showed a snapshot of the previous history entry, then popstate played the collapse). In a plain browser (and on
Android/desktop PWA) layers keep their history entries: the browser/system «back» closes the top layer.

Run externally with Playwright; APP_DIR selects checkout. No Telegram host (stand on 127.0.0.1 = DEV, no gate).
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "18982"))
URL = f"http://127.0.0.1:{PORT}"
SID = "c3e5a7b9"
IPHONE_PWA = """Object.defineProperty(navigator,'standalone',{get:()=>true});
 Object.defineProperty(navigator,'userAgent',{get:()=>'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148'});"""


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def run(pw, mode):
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    ctx.add_init_script((IPHONE_PWA if mode == "iphone-pwa" else "") + "localStorage.setItem('cc-filter','all')")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(URL + "/app/", wait_until="load")
        n0 = page.evaluate("history.length")
        page.locator(f'.card[data-id="{SID}"]').click()
        expect(page.locator("#session-screen")).to_be_visible()
        page.wait_for_timeout(700)
        grew = page.evaluate("history.length") - n0
        if mode == "iphone-pwa":
            check(grew == 0, f"opening a session pushed {grew} history entries: the iOS edge swipe goes «back» over them")
            check(page.evaluate("!(history.state&&history.state.d)"), "history.state carries a layer depth")
            page.locator("#session-screen [data-back]:visible").first.click()
            expect(page.locator("#session-screen")).to_be_hidden()
        else:
            check(grew == 1, f"browser: opening a session pushed {grew} history entries, want 1 (browser back closes it)")
            page.go_back()
            expect(page.locator("#session-screen")).to_be_hidden()
        check(not errors, "page errors: " + "; ".join(errors))
    finally:
        browser.close()


def main():
    failures = 0
    with tempfile.TemporaryDirectory(prefix="cc-pwahist-") as tmp:
        data = Path(tmp) / "demo.json"
        data.write_text((ROOT.parent / "stand/demo2.json").read_text())
        server = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, DATA_FILE=str(data), PORT=str(PORT)), stdout=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    urllib.request.urlopen(URL + "/api/sessions", timeout=.3).close()
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("stand readiness timeout")
            with sync_playwright() as pw:
                for mode in ("iphone-pwa", "browser"):
                    try:
                        run(pw, mode)
                        print(f"PASS chromium {mode}")
                    except Exception as e:
                        failures += 1
                        print(f"FAIL chromium {mode}: {e}")
        finally:
            server.terminate()
            server.wait(timeout=5)
    print(f"TOTAL {'PASS' if not failures else 'FAIL'} ({failures} failed)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
