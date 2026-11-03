"""Browser acceptance on synthetic data; APP_DIR selects the static checkout."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / "stand/demo2.json"
PORT = int(os.environ.get("PORT", "18981"))
URL = f"http://127.0.0.1:{PORT}"
BASE = json.loads(SOURCE.read_text())


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def press(page, selector, *, touch=False, position=None):
    """Keep real actionability checks and report the hit target on timeout."""
    try:
        action = page.locator(selector).tap if touch else page.locator(selector).click
        action(**({"position": position} if position else {}))
    except Exception as error:
        print(f"ACTION FAIL {selector}: {error}", flush=True)
        try:
            targets = page.evaluate("""({selector,position}) => {
              const el=document.querySelector(selector), r=el&&el.getBoundingClientRect();
              const x=r&&r.left+(position?position.x:r.width/2);
              const y=r&&r.top+(position?position.y:r.height/2);
              const info=e=>{const s=getComputedStyle(e);return {
                tag:e.tagName,id:e.id,cls:e.className,state:e.dataset.state,
                rect:e.getBoundingClientRect().toJSON(),z:s.zIndex,
                pointer:s.pointerEvents,display:s.display,opacity:s.opacity};};
              return {target:el&&info(el),x,y,stack:r?document.elementsFromPoint(x,y).map(info):[],
                layers:Array.from(document.querySelectorAll('.lab-ask-root,.session-header,.mn-root,#shade,#sub-sheet')).map(info)};
            }""", {"selector": selector, "position": position})
            print("HIT TARGETS " + json.dumps(targets), flush=True)
        except Exception as diagnostic_error:
            print(f"HIT TARGETS unavailable: {diagnostic_error}", flush=True)
        raise


def ready(page):
    page.goto(URL + "/app/", wait_until="load")
    expect(page.locator("#list .card").first).to_be_visible()


def question(page):
    ready(page)
    page.locator('.card[data-id="a1c3e5f7"]').click()
    expect(page.locator('.lab-ask-root[data-state="open"]')).to_be_visible()
    page.wait_for_timeout(1100)


def collapse(page):
    press(page, ".lab-ask-scrim", touch=True, position={"x": 12, "y": 80})
    expect(page.locator('.lab-ask-root[data-state="pill"]')).to_be_visible()
    page.wait_for_timeout(500)


def d1(page, ctx, datafile):
    question(page)
    original = page.locator("#s-body").evaluate("e=>parseFloat(getComputedStyle(e).paddingBottom)")
    collapse(page)
    page.locator("#s-body").evaluate("e=>e.scrollTop=e.scrollHeight")
    page.wait_for_timeout(300)
    msg = page.locator("#s-hist .msg").last.bounding_box()
    pill = page.locator(".lab-ask-pill").bounding_box()
    pad = page.locator("#s-body").evaluate("e=>parseFloat(getComputedStyle(e).paddingBottom)")
    check(msg["y"] + msg["height"] <= pill["y"], f"last message bottom={msg['y']+msg['height']:.1f}, pill top={pill['y']:.1f}")
    check(pad >= pill["height"] + 12, f"bottom padding {pad} < pill+12")
    press(page, ".lab-ask-pill", touch=True)
    page.wait_for_timeout(700)
    check(page.locator("#s-body").evaluate("e=>parseFloat(getComputedStyle(e).paddingBottom)") == original, "padding not restored on open")
    collapse(page)
    page.keyboard.press("Escape")
    expect(page.locator("#session-screen")).to_be_hidden()
    page.wait_for_timeout(500)
    check(page.locator("#s-body").evaluate("e=>parseFloat(getComputedStyle(e).paddingBottom)") == original, "padding not restored after leaving")


def d2(page, ctx, datafile):
    # Оба языка, HTTP 500 и сетевой обрыв; затем ошибка с уже загруженным списком.
    page.add_init_script("localStorage.setItem('cc-lang', (document.cookie.match(/test_lang=(ru|en)/)||[])[1]||'en')")
    for lang, text, retry in [("en", "Couldn't load sessions", "Retry"), ("ru", "Не удалось загрузить сессии", "Повторить")]:
        for failure in ("500", "abort"):
            ctx.add_cookies([{"name": "fail_sessions", "value": "1", "url": URL}, {"name": "test_lang", "value": lang, "url": URL}])
            if failure == "abort":
                page.route("**/api/sessions", lambda route: route.abort())
            page.goto(URL + "/app/", wait_until="load")
            page.wait_for_timeout(3000)
            check(page.locator(".skel").count() == 0, f"{lang}/{failure}: skeletons remain after 3s")
            expect(page.locator("#list")).to_contain_text(text)
            button = page.get_by_role("button", name=retry, exact=True)
            expect(button).to_be_visible()
            check(button.bounding_box()["height"] >= 44, "retry target below 44px")
            ctx.clear_cookies()
            page.unroute("**/api/sessions")
            button.click()
            expect(page.locator("#list .card").first).to_be_visible()
            card = page.locator("#list .card").first.element_handle()
            # ошибка на стороне стенда (cookie): page.route в WebKit не перехватывает фоновый fetch опроса
            ctx.add_cookies([{"name": "fail_sessions", "value": "1", "url": URL}, {"name": "test_lang", "value": lang, "url": URL}])
            expect(page.locator("#toast")).to_contain_text(text, timeout=12000)
            expect(page.locator("#toast")).to_be_visible()
            check(card.evaluate("e=>e.isConnected"), "cached list replaced on error")
            ctx.clear_cookies()


def d3(page, ctx, datafile):
    # Esc работает и при фокусе в листе, и при фокусе снаружи.
    for focused in (False, True):
        question(page)
        if focused:
            page.locator(".lab-ask-opt").first.focus()
        page.keyboard.press("Escape")
        expect(page.locator("#session-screen")).to_be_visible()
        expect(page.locator('.lab-ask-root[data-state="pill"]')).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("#session-screen")).to_be_hidden()
        expect(page.locator("#list .card").first).to_be_visible()


def d4(page, ctx, datafile):
    for width, height in [(1440, 900), (390, 844)]:
        page.set_viewport_size({"width": width, "height": height})
        question(page)
        collapse(page)
        # Вверху сессии находится настоящая кнопка открытия листа.
        page.locator("#s-body").evaluate("e=>e.scrollTop=0")
        press(page, "#s-subs")
        expect(page.locator("#sub-sheet")).to_be_visible()
        page.wait_for_timeout(800)
        b = page.locator("#sub-sheet").bounding_box()
        col = page.locator("#s-body").bounding_box()
        check(b["width"] <= 720 if width >= 768 else abs(b["width"] - width) <= 1, f"viewport={width}, sheet width={b['width']}")
        check(abs(b["x"] + b["width"] / 2 - col["x"] - col["width"] / 2) <= 2, "sheet not centered on session")


def d5(page, ctx, datafile):
    ready(page)
    start = BASE["sessions"]["quota"]["h5"]
    # Сначала обычный список: прежний опрос уже должен обновлять квоту за 20с.
    for value, swiped in [(start + 3, False), (start + 7, True)]:
        if swiped and ctx.browser.browser_type.name == "webkit":
            print("SKIP D5 webkit swipe sub-check: no Touch constructor in desktop WebKit (check on device)", flush=True)
            continue
        if swiped:
            page.locator("#list .cw .card").first.evaluate("""el=>{
              const t=(x)=>new Touch({identifier:1,target:el,clientX:x,clientY:300});
              el.dispatchEvent(new TouchEvent('touchstart',{bubbles:true,touches:[t(300)]}));
              el.dispatchEvent(new TouchEvent('touchmove',{bubbles:true,touches:[t(160)]}));
              el.dispatchEvent(new TouchEvent('touchend',{bubbles:true,touches:[]}));
            }""")
            expect(page.locator(".cw.open")).to_have_count(1)
        card = page.locator("#list .cw .card").first.element_handle()
        updated = json.loads(SOURCE.read_text())
        updated["sessions"]["quota"]["h5"] = value
        datafile.write_text(json.dumps(updated))
        started = time.monotonic()
        deadline = time.monotonic() + 20
        expected = f"5 hours: {round(value)} %"
        while time.monotonic() < deadline:
            if page.locator('#lab-q .lqt').first.get_attribute("aria-label") == expected:
                break
            page.wait_for_timeout(100)
        else:
            raise AssertionError(f"quota did not reach {value}% within 20s; swiped={swiped}")
        page.wait_for_timeout(1000)
        # Проверяем конечное положение цифр, а не только aria-label.
        visible_number = page.locator('#lab-q .lqt').first.evaluate("""e=>Array.from(e.querySelectorAll('.lqd')).map(d=>{
          if(d.classList.contains('z'))return '';
          const r=d.getBoundingClientRect();return Array.from(d.querySelectorAll('b')).find(b=>{
            const q=b.getBoundingClientRect();return Math.abs(q.top-r.top)<2;
          })?.textContent;
        }).join('')""")
        check(visible_number == str(round(value)), f"visible odometer={visible_number}, expected={value}")
        if not swiped:
            print(f"PASS D5 {ctx.browser.browser_type.name} ordinary refresh in {time.monotonic()-started:.1f}s", flush=True)
        if swiped:
            check(card.evaluate("e=>e.isConnected"), "swiped card replaced")
            expect(page.locator(".cw.open")).to_have_count(1)
    # Неизменившиеся ответы не должны пересоздавать карточки.
    if ctx.browser.browser_type.name != "webkit":  # тап закрывает смахнутую карточку; в WebKit свайпа не было
        page.locator("#list .cw .card").first.tap()
        page.wait_for_timeout(900)
    stable = page.locator("#list .cw .card").first.element_handle()
    if ctx.browser.browser_type.name == "webkit":
        page.wait_for_timeout(10000)  # WebKit не отдаёт события фонового fetch; опрос списка — раз в 9 с
    else:
        with page.expect_response(lambda r: r.url.endswith('/api/sessions'), timeout=12000):
            pass
        page.wait_for_timeout(200)
    check(stable.evaluate("e=>e.isConnected"), "unchanged list recreated on poll")
    first = page.locator("#list .card").first
    first.click() if ctx.browser.browser_type.name == "webkit" else first.tap()
    expect(page.locator("#session-screen")).to_be_visible()


def main():
    failures = 0
    count = 0
    engines = os.environ.get("BROWSERS", "chromium,webkit").split(",")
    reduced = os.environ.get("REDUCED_MOTION", "no-preference")
    with tempfile.TemporaryDirectory(prefix="cc-front-") as tmp:
        datafile = Path(tmp) / "demo.json"
        datafile.write_text(json.dumps(BASE))
        env = dict(os.environ, DATA_FILE=str(datafile), PORT=str(PORT))
        env.pop("FAIL_SESSIONS", None)
        server = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=env, stdout=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if server.poll() is not None:
                    raise RuntimeError("test server failed to start")
                try:
                    urllib.request.urlopen(URL + "/api/sessions", timeout=.3).close()
                    break
                except OSError:
                    time.sleep(.1)
            with sync_playwright() as pw:
                for engine in engines:
                    browser = getattr(pw, engine).launch()
                    try:
                        for ident, test in enumerate((d1, d2, d3, d4, d5), 1):
                            datafile.write_text(json.dumps(BASE))
                            ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="en-US", reduced_motion=reduced)
                            ctx.route("https://telegram.org/**", lambda r: r.fulfill(body=""))
                            page = ctx.new_page()
                            errors = []
                            page.on("pageerror", lambda e: errors.append(str(e)))
                            count += 1
                            try:
                                test(page, ctx, datafile)
                                check(not errors, f"page errors: {errors}")
                                description = {1: "pill clearance and padding reset", 2: "localized error, retry, cached list", 3: "Escape collapses then exits", 4: "sheet desktop/mobile geometry", 5: "quota refresh, swipe and stable cards"}[ident]
                                print(f"PASS D{ident} {engine} {description} ({reduced})", flush=True)
                            except Exception as e:
                                failures += 1
                                print(f"FAIL D{ident} {engine} " + (str(e) if os.environ.get("VERBOSE") else str(e).splitlines()[0]), flush=True)
                            finally:
                                ctx.close()
                    finally:
                        browser.close()
        finally:
            server.terminate()
            server.wait(timeout=5)
    print(f"TOTAL {count-failures} PASS, {failures} FAIL", flush=True)
    return bool(failures)


if __name__ == "__main__":
    try:
        result = main()
    except Exception as e:
        print(f"FAIL INFRA {type(e).__name__}: {e}", flush=True)
        print("TOTAL BLOCKED: browser checks not completed", flush=True)
        result = 2
    sys.exit(result)
