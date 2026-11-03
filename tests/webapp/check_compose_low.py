"""The session field sits low on an iPhone: the bottom safe area is carried once, by the quota line under the
field, and while typing the quota line is gone so the field rests on the keyboard. Owner 20.10 08:38 «поле
слишком высоко» (iPhone frames IMG_5135/5136: ~108 pt from the bottom at rest, a ~70 pt gap above the keyboard)
— the safe area was added both to the field's margin and to the quota line.

Chromium only: the safe area is emulated through CDP (Emulation.setSafeAreaInsetsOverride, bottom 34 like an
iPhone). Run externally with Playwright; APP_DIR selects checkout.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "18981"))
URL = f"http://127.0.0.1:{PORT}"
SID = "c3e5a7b9"
GAP = """(()=>{const c=document.querySelector('#session-screen .compose'),q=document.getElementById('lab-sq')||document.getElementById('s-quota');
 return {gap:innerHeight-c.getBoundingClientRect().bottom,quota:!!q&&!q.hidden&&getComputedStyle(q).display!=='none'}})()"""


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def main():
    failures = 0
    with tempfile.TemporaryDirectory(prefix="cc-low-") as tmp:
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
                for theme in ("light", "dark"):
                    browser = pw.chromium.launch()
                    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True)
                    ctx.add_init_script("localStorage.setItem('cc-filter','all')")
                    ctx.route("https://telegram.org/**", lambda route: route.fulfill(body=""))
                    page = ctx.new_page()
                    ctx.new_cdp_session(page).send("Emulation.setSafeAreaInsetsOverride", {"insets": {"top": 59, "bottom": 34}})
                    try:
                        page.goto(f"{URL}/app/?theme=tg-{theme}#s={SID}", wait_until="load")
                        page.wait_for_timeout(1500)
                        rest = page.evaluate(GAP)
                        check(rest["gap"] <= 64, f"field {rest['gap']:.0f} px above the bottom at rest (safe area 34 counted twice?)")
                        page.evaluate("document.getElementById('s-text').focus()")
                        page.wait_for_timeout(400)
                        typing = page.evaluate(GAP)
                        check(not typing["quota"], "quota line stays between the field and the keyboard")
                        check(typing["gap"] <= 12, f"field {typing['gap']:.0f} px above the keyboard edge while typing")
                        print(f"PASS chromium {theme}: rest {rest['gap']:.0f} px, typing {typing['gap']:.0f} px")
                    except Exception as e:
                        failures += 1
                        print(f"FAIL chromium {theme}: {e}")
                    finally:
                        browser.close()
        finally:
            server.terminate()
            server.wait(timeout=5)
    print(f"TOTAL {'PASS' if not failures else 'FAIL'} ({failures} failed)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
