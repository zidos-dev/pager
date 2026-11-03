"""Session open/close on a phone is one transition: the Clio morph (lab-open), not the product slide underneath it.
Owner 20.10 03:55 «выход назад из сессии» looked worse: two systems drove the same layers (paintNav wrote translate3d
and will-change into both screens every frame under the morph, navdim under it) and the screen stayed a still card
for ~100 ms after the morph ended. Every frame is sampled: no inline transform / will-change on #session-screen or
#list-screen, #navdim never shown; the screen hides right after the morph (session:closed); edge swipe follows the
finger through --lo-p and cancels back to rest.

Run externally with Playwright; APP_DIR selects checkout.
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
SAMPLER = """window.__nav=[];window.__t0=null;document.addEventListener('session:close',()=>{if(window.__t0===null)window.__t0=performance.now()});
(function f(){const L=document.getElementById('list-screen'),s=document.getElementById('session-screen'),n=document.getElementById('navdim');
 window.__nav.push({t:performance.now(),inl:s.style.transform+L.style.transform,wc:s.style.willChange+L.style.willChange,
  dim:n?+getComputedStyle(n).opacity*(getComputedStyle(n).display==='none'?0:1):0,hid:s.hidden});requestAnimationFrame(f)})()"""
TOUCH = """([typ,x])=>{const t=new Touch({identifier:1,target:document.body,clientX:x,clientY:400});
 document.dispatchEvent(new TouchEvent(typ,{touches:typ==='touchend'?[]:[t],changedTouches:[t],bubbles:true,cancelable:true}))}"""


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def clean(frames, what):
    bad = [f for f in frames if f["inl"] or f["wc"] or f["dim"] > 0]
    check(not bad, f"{what}: product slide under the morph in {len(bad)}/{len(frames)} frames, first {bad[:1]}")


def opened(page):
    page.goto(URL + "/app/", wait_until="load")
    page.wait_for_timeout(800)
    page.evaluate(SAMPLER)
    page.locator(f'.card[data-id="{SID}"]').click()
    expect(page.locator("#session-screen")).to_be_visible()
    page.wait_for_timeout(1200)
    clean(page.evaluate("window.__nav.splice(0)"), "open")


def button_back(page):
    opened(page)
    page.locator("#session-screen [data-back]").first.click()
    expect(page.locator("#session-screen")).to_be_hidden()
    page.wait_for_timeout(300)
    frames = page.evaluate("window.__nav")
    clean(frames, "button back")
    t0 = page.evaluate("window.__t0")
    out = page.evaluate("parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--m-expand-out-t'))") or 355
    hid = next(f["t"] for f in frames if f["hid"])
    check(hid - t0 <= out + 120, f"screen hid {hid - t0:.0f} ms after session:close, morph ends at {out:.0f} ms")


def swipe(page, commit):
    opened(page)
    page.evaluate(TOUCH, ["touchstart", 10])
    xs = [20, 60, 120, 180, 240] if commit else [20, 40, 60, 50]
    progress = []
    for x in xs:
        page.evaluate(TOUCH, ["touchmove", x])
        page.wait_for_timeout(30)
        progress.append(page.evaluate("parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--lo-p'))"))
    check(progress[-2] < progress[0] < 1, f"--lo-p does not follow the finger: {progress}")
    page.evaluate(TOUCH, ["touchend", xs[-1]])
    page.wait_for_timeout(900)
    clean(page.evaluate("window.__nav"), "swipe " + ("commit" if commit else "cancel"))
    if commit:
        expect(page.locator("#session-screen")).to_be_hidden()
    else:
        expect(page.locator("#session-screen")).to_be_visible()
        check("lo-rest" in page.evaluate("document.documentElement.className"), "cancelled swipe did not settle open")


def main():
    failures = 0
    engines = [n.strip() for n in os.environ.get("BROWSERS", "chromium,webkit").split(",") if n.strip()]
    with tempfile.TemporaryDirectory(prefix="cc-nav-") as tmp:
        data = Path(tmp) / "demo.json"
        data.write_text((ROOT.parent / "stand/demo2.json").read_text())
        env = dict(os.environ, DATA_FILE=str(data), PORT=str(PORT))
        server = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=env, stdout=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if server.poll() is not None:
                    raise RuntimeError("stand failed to start")
                try:
                    urllib.request.urlopen(URL + "/api/sessions", timeout=.3).close()
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("stand readiness timeout")
            with sync_playwright() as pw:
                for engine in engines:
                    cases = [("button back", button_back)]
                    if engine != "webkit":  # desktop WebKit has no Touch constructor
                        cases += [("swipe commit", lambda p: swipe(p, True)), ("swipe cancel", lambda p: swipe(p, False))]
                    else:
                        print("SKIP webkit swipe: no Touch constructor in desktop WebKit (check on device)", flush=True)
                    for name, case in cases:
                        browser = getattr(pw, engine).launch()
                        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="en-US")
                        ctx.add_init_script("localStorage.setItem('cc-lang','en');localStorage.setItem('cc-filter','all')")
                        ctx.route("https://telegram.org/**", lambda route: route.fulfill(body=""))
                        page = ctx.new_page()
                        errors = []
                        page.on("pageerror", lambda e: errors.append(str(e)))
                        try:
                            case(page)
                            check(not errors, "page errors: " + "; ".join(errors))
                            print(f"PASS {engine} {name}")
                        except Exception as e:
                            failures += 1
                            print(f"FAIL {engine} {name}: {e}")
                        finally:
                            browser.close()
        finally:
            server.terminate()
            server.wait(timeout=5)
    print(f"TOTAL {'PASS' if not failures else 'FAIL'} ({failures} failed)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
