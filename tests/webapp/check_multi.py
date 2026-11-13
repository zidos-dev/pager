"""Multi-select question sheet: tapping an option marks it, the sheet stays open, "next" sends.

Run externally with Playwright; APP_DIR selects checkout. Reuses State from check_own_answer.
"""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

from check_own_answer import BASE, PORT, URL, SID, OPEN, State, check

ROOT = Path(__file__).resolve().parent
DATA = deepcopy(BASE)
DATA["session"][SID]["question"]["multi"] = True
for row in DATA["sessions"]["sessions"]:
    if row["id"] == SID and row.get("question"):
        row["question"]["multi"] = True


def multi(page, state):
    page.goto(URL + "/app/?theme=tg-light", wait_until="load")
    page.locator(f'.card[data-id="{SID}"]').click()
    root = page.locator(OPEN)
    expect(root).to_be_visible()
    page.wait_for_timeout(1000)
    check("multi" in root.get_attribute("class").split(), "root lacks multi class")
    nxt = root.locator('.lab-ask-btn[data-act="next"]')
    expect(nxt).to_be_visible()
    root.locator(".lab-ask-opt").first.click()
    state.wait(page, lambda: bool(state.records(state.answers)), "option POST missing")
    check(state.records(state.answers) == [{"action": "option", "index": 1}], "unexpected ANSWER_LOG: " + state.answers.read_text())
    state.poll(page)
    root = page.locator(OPEN)
    expect(root).to_be_visible()
    check(page.locator('.lab-ask-root[data-state="pill"]').count() == 0, "sheet collapsed to pill")
    expect(root.locator('.lab-ask-opt[data-k="0"]')).to_have_attribute("aria-pressed", "true")
    expect(root.locator('.lab-ask-btn[data-act="next"]')).to_be_visible()
    root.locator('.lab-ask-btn[data-act="next"]').click()
    state.wait(page, lambda: len(state.records(state.answers)) > 1, "next POST missing")
    check(state.records(state.answers)[-1] == {"action": "next"}, "last ANSWER_LOG is not next: " + state.answers.read_text())


def server_error(page, state):
    # 12.11 iPhone: a 500 on «next» showed «No connection» and the toast covered the sheet's main button
    page.context.add_cookies([{"name": "answer_reply", "value": "internal", "url": URL}])
    try:
        page.goto(URL + "/app/?theme=tg-light", wait_until="load")
        page.locator(f'.card[data-id="{SID}"]').click()
        root = page.locator(OPEN)
        expect(root).to_be_visible()
        page.wait_for_timeout(1000)
        root.locator(".lab-ask-opt").first.click()  # a multi-select mark keeps the sheet up, as on the owner's phone
        toast = page.locator("#toast")
        expect(toast).to_be_visible()
        text = toast.inner_text()
        check("connection" not in text.lower(), "a server error reads as a network one: " + text)
        page.wait_for_timeout(400)
        buttons = page.locator(OPEN + " .lab-ask-btn").all()
        check(len(buttons) >= 2, "the sheet went away after a failed mark")
        expect(page.locator(OPEN + ' .lab-ask-opt[data-k="0"]')).to_have_attribute("aria-pressed", "false", timeout=4000)  # the server did not take it
        t = toast.bounding_box()
        for b in buttons:
            r = b.bounding_box()
            if r and r["width"] > 0:
                check(t["y"] + t["height"] <= r["y"] + .5 or t["y"] >= r["y"] + r["height"] - .5,
                      "toast covers the sheet button " + b.inner_text())
    finally:
        page.context.clear_cookies()


def slow_answer(page, state):
    # 13.11: a tap made while the previous POST is in flight was dropped; the sheet hung «busy»
    page.context.add_cookies([{"name": "answer_reply", "value": "next", "url": URL}, {"name": "answer_delay", "value": "1.5", "url": URL}])
    try:
        for second in (False, True):
            state.answers.write_text("")
            state.write(DATA)
            page.goto(URL + "/app/?theme=tg-light", wait_until="load")
            page.locator(f'.card[data-id="{SID}"]').click()
            root = page.locator(OPEN)
            expect(root).to_be_visible()
            page.wait_for_timeout(1000)
            root.locator(".lab-ask-opt").nth(0).click()
            page.wait_for_timeout(300)
            if second:
                root.locator(".lab-ask-opt").nth(1).click()
                state.wait(page, lambda: len(state.records(state.answers)) == 2, "second mark POST missing", timeout=8)
                check(state.records(state.answers) == [{"action": "option", "index": 1}, {"action": "option", "index": 2}], "marks: " + state.answers.read_text())
                page.wait_for_timeout(1500)
                state.poll(page)
                root = page.locator(OPEN)
                expect(root.locator('.lab-ask-opt[data-k="0"]')).to_have_attribute("aria-pressed", "true", timeout=4000)
                expect(root.locator('.lab-ask-opt[data-k="1"]')).to_have_attribute("aria-pressed", "true", timeout=4000)
            else:
                root.locator('.lab-ask-btn[data-act="next"]').click()
                state.wait(page, lambda: len(state.records(state.answers)) == 2, "next POST missing", timeout=8)
                check(state.records(state.answers) == [{"action": "option", "index": 1}, {"action": "next"}], "mark+next: " + state.answers.read_text())
                expect(page.locator(OPEN)).to_contain_text("cache expiry", timeout=8000)
                check("busy" not in page.locator(OPEN).get_attribute("class").split(), "sheet hangs busy on step 2")
    finally:
        page.context.clear_cookies()


def main():
    failures = 0
    engines = [n.strip() for n in os.environ.get("BROWSERS", "chromium,webkit").split(",") if n.strip()]
    with tempfile.TemporaryDirectory(prefix="cc-multi-") as tmp:
        state = State(Path(tmp))
        env = dict(os.environ, DATA_FILE=str(state.data), ANSWER_LOG=str(state.answers), SEND_LOG=str(state.sends), GET_LOG=str(state.gets), PORT=str(PORT))
        env.pop("FAIL_SESSIONS", None)
        for path in (state.answers, state.sends, state.gets):
            path.write_text("")
        state.write(DATA)
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
                    for path in (state.answers, state.sends, state.gets):
                        path.write_text("")
                    state.write(DATA)
                    browser = getattr(pw, engine).launch()
                    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="en-US", color_scheme="light")
                    ctx.add_init_script("localStorage.setItem('cc-lang','en')")
                    ctx.route("https://telegram.org/**", lambda route: route.fulfill(body=""))
                    page = ctx.new_page()
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    try:
                        multi(page, state)
                        state.write(DATA)
                        server_error(page, state)
                        slow_answer(page, state)
                        check(not errors, f"page errors: {errors}")
                        print(f"PASS {engine} multi-select", flush=True)
                    except Exception as error:
                        failures += 1
                        print(f"FAIL {engine} multi-select: {(str(error) if os.environ.get('VERBOSE') else str(error).splitlines()[0])}", flush=True)
                    finally:
                        ctx.close()
                        browser.close()
        finally:
            server.terminate()
            server.wait(timeout=5)
    return int(bool(failures))


if __name__ == "__main__":
    try:
        result = main()
    except Exception as error:
        print(f"FAIL INFRA {type(error).__name__}: {error}", flush=True)
        result = 2
    sys.exit(result)
