"""On an iPhone the app leads «back» itself: Telegram's BackButton stays hidden, so the edge swipe reaches the app
(the layers morph follows the finger) instead of Telegram's own back swipe sliding a snapshot of the previous page
before the collapse plays (owner 20.10 08:49, IMG_5134). Every layer then shows exactly one back control of its
own. On Android Telegram's BackButton is the system back and stays.

Run externally with Playwright; APP_DIR selects checkout. Telegram host is stubbed (as in check_header_menu).
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
PORT = int(os.environ.get("PORT", "18981"))
URL = f"http://127.0.0.1:{PORT}"
SID = "c3e5a7b9"
VISIBLE_BACKS = """sel=>[...document.querySelectorAll(sel)].filter(b=>{const r=b.getBoundingClientRect(),c=getComputedStyle(b);
 return r.width>0&&r.height>0&&c.visibility!=='hidden'&&c.display!=='none'&&+c.opacity>0&&!b.closest('[hidden]')}).length"""


def stub(platform):
    return """window.__bb=[];window.Telegram={WebApp:{initData:'synthetic',platform:'%s',colorScheme:'light',
      ready(){},expand(){},disableVerticalSwipes(){},onEvent(){},offEvent(){},
      BackButton:{show(){window.__bb.push('show')},hide(){window.__bb.push('hide')},onClick(fn){window.__back=fn;},offClick(){}},
      MainButton:{show(){},hide(){},setText(){},onClick(){},offClick(){},setParams(){}},
      HapticFeedback:{impactOccurred(){},notificationOccurred(){},selectionChanged(){}}}};""" % platform


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def run(pw, platform):
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    ctx.add_init_script(stub(platform) + "localStorage.setItem('cc-filter','all')")
    ctx.route("https://telegram.org/**", lambda route: route.fulfill(body=""))
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(URL + "/app/", wait_until="load")
        page.locator(f'.card[data-id="{SID}"]').click()
        expect(page.locator("#session-screen")).to_be_visible()
        page.wait_for_timeout(900)
        shows = page.evaluate("window.__bb.filter(x=>x==='show').length")
        backs = page.evaluate(VISIBLE_BACKS, "#session-screen [data-back], #session-screen .back")
        if platform == "ios":
            check(shows == 0, f"Telegram BackButton shown {shows}× on iOS: its edge swipe takes over")
            check(backs == 1, f"session shows {backs} back controls of its own, want 1")
            page.locator("#session-screen [data-back]:visible").first.click()
            expect(page.locator("#session-screen")).to_be_hidden()
            page.locator("#new-btn, #lb-new").first.click()
            expect(page.locator("#new-screen")).to_be_visible()
            page.wait_for_timeout(600)
            nb = page.evaluate(VISIBLE_BACKS, "#new-screen [data-back]")
            check(nb == 1, f"new session screen shows {nb} back controls, want 1")
        else:
            check(shows >= 1, "Android: Telegram BackButton not shown (it is the system back)")
        check(not errors, "page errors: " + "; ".join(errors))
    finally:
        browser.close()


def main():
    failures = 0
    with tempfile.TemporaryDirectory(prefix="cc-iosback-") as tmp:
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
                for platform in ("ios", "android"):
                    try:
                        run(pw, platform)
                        print(f"PASS chromium {platform}")
                    except Exception as e:
                        failures += 1
                        print(f"FAIL chromium {platform}: {e}")
        finally:
            server.terminate()
            server.wait(timeout=5)
    print(f"TOTAL {'PASS' if not failures else 'FAIL'} ({failures} failed)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
