"""Back from a layer: the lower screen's buttons return with the slide, and a back gesture does not swipe a list row.

1. «New session» -> type a task -> back: on the next frames the «Start» button is gone, «+» is back
   and the list is unlocked, while the layer is still sliding (owner's iPhone frames 20.10: «Start»
   hung over the list, «+» and the filter capsule came back one after another).
2. Back from a session, then a horizontal touch on a list row while the layer slides: the row stays put
   (owner's frame: «В архив» opened under the back gesture).

Run externally with Playwright; APP_DIR selects checkout. Reuses State from check_own_answer.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

from check_own_answer import BASE, PORT, URL, State, check

ROOT = Path(__file__).resolve().parent


def new_screen_back(page):
    page.goto(URL + "/app/?theme=tg-light", wait_until="load")
    page.wait_for_timeout(800)
    page.evaluate("document.getElementById('new-btn').click()")
    expect(page.locator("#new-screen")).to_be_visible()
    page.wait_for_timeout(700)
    page.fill("#n-text", "check the logs")
    expect(page.locator("#fab")).to_be_visible()
    s = page.evaluate("""() => new Promise(res => {
      document.querySelector('#new-screen [data-back]').click();
      requestAnimationFrame(() => requestAnimationFrame(() => res({
        fab: document.getElementById('fab').hidden,
        plus: document.getElementById('new-btn').hidden,
        locked: document.documentElement.classList.contains('locked'),
        sliding: !!document.getElementById('new-screen').style.transform
      })));
    })""")
    check(s["sliding"], f"layer is not sliding two frames after back: {s}")
    check(s["fab"], f"«Start» still shown while the layer slides away: {s}")
    check(not s["plus"], f"«+» hidden while the layer slides away: {s}")
    check(not s["locked"], f"list still locked while the layer slides away: {s}")


def row_swipe_under_back(page):
    page.goto(URL + "/app/?theme=tg-light", wait_until="load")
    page.wait_for_timeout(800)
    page.locator("#list .card").nth(2).click()
    expect(page.locator("#session-screen")).to_be_visible()
    page.wait_for_timeout(900)
    moved = page.evaluate("""() => new Promise(res => {
      const row = document.querySelector('#list .cw'), card = row.querySelector('.card'), r = card.getBoundingClientRect();
      const y = r.top + r.height / 2;
      const touch = x => new Touch({identifier: 1, target: card, clientX: x, clientY: y});
      const fire = (type, x) => card.dispatchEvent(new TouchEvent(type, {bubbles: true, cancelable: true,
        touches: type === 'touchend' ? [] : [touch(x)], changedTouches: [touch(x)]}));
      document.querySelector('#session-screen [data-back]').click();
      requestAnimationFrame(() => {
        fire('touchstart', 300);
        for (let x = 290; x >= 200; x -= 10) fire('touchmove', x);
        res({transform: card.style.transform, act: row.classList.contains('act')});
        fire('touchend', 200);
      });
    })""")
    check(not moved["transform"] and not moved["act"], f"list row swiped under the back gesture: {moved}")


CASES = (("new-screen back order", new_screen_back), ("no row swipe under back", row_swipe_under_back))


def main():
    failures = 0
    engines = [n.strip() for n in os.environ.get("BROWSERS", "chromium,webkit").split(",") if n.strip()]
    with tempfile.TemporaryDirectory(prefix="cc-back-") as tmp:
        state = State(Path(tmp))
        env = dict(os.environ, DATA_FILE=str(state.data), ANSWER_LOG=str(state.answers), SEND_LOG=str(state.sends), GET_LOG=str(state.gets), PORT=str(PORT))
        env.pop("FAIL_SESSIONS", None)
        for path in (state.answers, state.sends, state.gets):
            path.write_text("")
        state.write(BASE)
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
                    for name, case in CASES:
                        # Touch/TouchEvent constructors exist only where touch is emulated (Chromium); WebKit on Linux has none
                        if case is row_swipe_under_back and engine != "chromium":
                            continue
                        browser = getattr(pw, engine).launch()
                        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=engine != "firefox", has_touch=True, locale="en-US", color_scheme="light")
                        ctx.add_init_script("localStorage.setItem('cc-lang','en')")
                        ctx.route("https://telegram.org/**", lambda route: route.fulfill(body=""))
                        page = ctx.new_page()
                        errors = []
                        page.on("pageerror", lambda e: errors.append(str(e)))
                        try:
                            case(page)
                            check(not errors, f"page errors: {errors}")
                            print(f"PASS {engine} {name}", flush=True)
                        except Exception as error:
                            failures += 1
                            print(f"FAIL {engine} {name}: {(str(error) if os.environ.get('VERBOSE') else str(error).splitlines()[0])}", flush=True)
                        finally:
                            ctx.close()
                            browser.close()
        finally:
            server.terminate()
            server.wait(timeout=5)
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
