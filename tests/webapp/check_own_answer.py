"""Own-answer acceptance. Run externally with Playwright; APP_DIR selects checkout.

Receipts come from the stand, not request interception: desktop WebKit does not
reliably expose background fetch events. No real session data is modified.
"""
from copy import deepcopy
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
BASE = json.loads((ROOT.parent / "stand/demo2.json").read_text())
PORT = int(os.environ.get("PORT", "18981"))
URL = f"http://127.0.0.1:{PORT}"
SID = "a1c3e5f7"
TEXT = "my own answer"
NEXT = "Which cache expiry should we use?"
OPEN = '.lab-ask-root[data-state="open"]'
FIELD = ".lab-ask-own-text"
SEND = '.lab-ask-btn[data-act="own-send"]'
CANCEL = '.lab-ask-btn[data-act="own-cancel"]'


def check(ok, message):
    if not ok:
        raise AssertionError(message)


class State:
    def __init__(self, folder):
        self.data = folder / "demo.json"
        self.answers = folder / "answers.jsonl"
        self.sends = folder / "sends.jsonl"
        self.gets = folder / "gets.jsonl"

    def reset(self):
        self.write(BASE)
        for path in (self.answers, self.sends, self.gets):
            path.write_text("")

    def write(self, data):
        data = deepcopy(data)
        data["_test_case"] = time.monotonic_ns()
        temporary = self.data.with_suffix(".reset")
        temporary.write_text(json.dumps(data, ensure_ascii=False))
        temporary.replace(self.data)

    def records(self, path):
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def wait(self, page, predicate, message, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            page.wait_for_timeout(25)
        raise AssertionError(message)

    def posted(self, page, text=TEXT):
        self.wait(page, lambda: bool(self.records(self.answers)), "own answer POST missing")
        check(self.records(self.answers) == [{"action": "own", "text": text}], "unexpected/duplicate ANSWER_LOG body: " + self.answers.read_text())

    def poll(self, page):
        # GET_LOG is reliable in WebKit where expect_response is not.
        before = len(self.records(self.gets))
        self.wait(page, lambda: len(self.records(self.gets)) > before, "session poll missing", timeout=8)
        page.wait_for_timeout(150)


def cookie(ctx, reply, delay="0"):
    ctx.add_cookies([{"name": "answer_reply", "value": reply, "url": URL},
                     {"name": "answer_delay", "value": delay, "url": URL}])


def opened(page, theme):
    page.goto(URL + "/app/?theme=tg-" + theme, wait_until="load")
    expect(page.locator(f'.card[data-id="{SID}"]')).to_be_visible()
    page.locator(f'.card[data-id="{SID}"]').click()
    root = page.locator(OPEN)
    expect(root).to_be_visible()
    page.wait_for_timeout(1000)
    root.locator('.lab-ask-btn[data-act="own"]').click()
    expect(root).to_be_visible()
    expect(root.locator(".lab-ask-own")).to_be_visible()
    expect(root.locator(FIELD)).to_be_visible()
    expect(root.locator(FIELD)).to_be_focused()
    check("own-open" in root.get_attribute("class").split(), "editor root lacks own-open")
    for selector in (SEND, CANCEL):
        box = root.locator(selector).bounding_box()
        check(box and box["height"] >= 44, selector + " target below 44px")
    check(root.locator(FIELD).evaluate("e=>parseFloat(getComputedStyle(e).fontSize)") >= 16, "textarea font may trigger iOS zoom")
    return root


def editor(page, ctx, state, theme, lang, engine):
    root = opened(page, theme)
    expect(root.locator(SEND)).to_have_text("Отправить" if lang == "ru" else "Send")
    expect(root.locator(CANCEL)).to_have_text("Отмена" if lang == "ru" else "Cancel")
    root.locator(FIELD).fill(TEXT)
    state.poll(page)
    expect(page.locator(OPEN).locator(FIELD)).to_have_value(TEXT)
    expect(page.locator(OPEN).locator(FIELD)).to_be_focused()
    path = ROOT / f"own-{engine}-{theme}-{lang}.png"
    page.screenshot(path=str(path))
    if engine == "chromium" and lang == "en":
        page.screenshot(path=str(ROOT / f"own-{theme}.png"))


def response(reply):
    def run(page, ctx, state, theme, lang, engine):
        cookie(ctx, reply, "1.5")
        root = opened(page, theme)
        root.locator(FIELD).fill(TEXT)
        root.locator(SEND).click()
        state.posted(page)
        expect(root.locator(SEND)).to_be_disabled()
        expect(root).to_be_visible()
        root.locator(FIELD).focus()
        for _ in range(3):
            page.keyboard.press("Control+Enter")
        state.posted(page)
        if reply in ("fail", "network"):
            expect(root.locator(".lab-ask-own-error")).to_be_visible()
            if reply == "fail":
                expect(root.locator(".lab-ask-own-error")).to_contain_text("синтетическая ошибка")
            else:
                check(bool(root.locator(".lab-ask-own-error").inner_text().strip()), "network error is empty")
            expect(root.locator(FIELD)).to_have_value(TEXT)
            expect(root.locator(SEND)).to_be_enabled()
            state.poll(page)
            expect(page.locator(OPEN).locator(FIELD)).to_have_value(TEXT)
            expect(page.locator(OPEN).locator(".lab-ask-own-error")).to_be_visible()
        elif reply == "next":
            expect(page.locator(OPEN).locator(".lab-ask-q")).to_have_text(NEXT)
            expect(page.locator(OPEN).locator(FIELD)).to_be_hidden()
            state.poll(page)
            expect(page.locator(OPEN).locator(".lab-ask-q")).to_have_text(NEXT)
            page.locator(OPEN).locator('[data-act="own"]').click()
            expect(page.locator(OPEN).locator(FIELD)).to_have_value("")
        else:
            expect(page.locator(OPEN)).to_have_count(0)
            expect(page.locator("#toast")).to_have_text("Ответ отправлен" if lang == "ru" else "Sent")
            expect(page.locator("#toast")).to_be_visible()
            state.poll(page)
            expect(page.locator(".lab-ask-root")).to_have_count(0)
        state.posted(page)
    return run


def cancel_escape(page, ctx, state, theme, lang, engine):
    root = opened(page, theme)
    root.locator(FIELD).fill(TEXT)
    root.locator(CANCEL).click()
    expect(root).to_be_visible()
    expect(root.locator(FIELD)).to_be_hidden()
    expect(root.locator(".lab-ask-opt").first).to_be_visible()
    root.locator('[data-act="own"]').click()
    root.locator(FIELD).fill(TEXT)
    page.keyboard.press("Escape")
    expect(root).to_be_visible()
    expect(root.locator(FIELD)).to_be_hidden()
    page.keyboard.press("Escape")
    expect(page.locator('.lab-ask-root[data-state="pill"]')).to_be_visible()
    expect(page.locator("#session-screen")).to_be_visible()
    check(not state.records(state.answers), "cancel/Escape submitted answer")


def keyboard(page, ctx, state, theme, lang, engine):
    data = deepcopy(BASE)
    data["session"][SID]["question"]["question"] = (
        "Please choose the cache location and explain the expiry policy for all "
        "application instances, including restart recovery, memory limits, and "
        "shared deployment constraints. " * 3)
    state.write(data)
    cookie(ctx, "fail")
    root = opened(page, theme)
    text = "First line\nSecond line\nThird line\nFourth line\nFifth line"
    root.locator(FIELD).fill(text)
    root.locator(SEND).click()
    state.posted(page, text)
    expect(root.locator(".lab-ask-own-error")).to_be_visible()
    root.locator(FIELD).focus()
    # Product focus/blur callbacks update --vvh at 60/300/350ms. Apply the
    # keyboard emulation only after they have settled so it cannot be undone.
    page.wait_for_timeout(650)
    page.locator("html").evaluate("e=>e.style.setProperty('--vvh','480px')")
    page.wait_for_timeout(650)
    for selector in (FIELD, SEND):
        box = root.locator(selector).bounding_box()
        check(box and box["y"] >= 0 and box["y"] + box["height"] <= 480.5,
              f"{selector} outside keyboard viewport: {box}")
    expect(root.locator(FIELD)).to_have_value(text)
    expect(root.locator(".lab-ask-own-error")).to_be_visible()
    page.screenshot(path=str(ROOT / f"own-kb-{engine}-{theme}-{lang}.png"))
    if engine == "chromium" and theme == "light" and lang == "en":
        page.screenshot(path=str(ROOT / "own-kb.png"))


def textarea_gesture(page, ctx, state, theme, lang, engine):
    root = opened(page, theme)
    text = "\n".join(f"Scrollable answer line {i}" for i in range(20))
    field = root.locator(FIELD)
    field.fill(text)
    page.wait_for_timeout(650)
    box = field.bounding_box()
    before = root.locator(".lab-ask-sheet").bounding_box()
    # A drag originating in a native textarea must stay a text interaction,
    # including when its internal overflow reaches an edge.
    x = box["x"] + box["width"] / 2
    y = box["y"] + min(20, box["height"] / 3)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y + 120, steps=12)
    page.mouse.up()
    page.wait_for_timeout(700)
    expect(root).to_be_visible()
    expect(root.locator(FIELD)).to_have_value(text)
    after = root.locator(".lab-ask-sheet").bounding_box()
    check(abs(after["y"] - before["y"]) < 2, f"textarea gesture moved sheet: {before} -> {after}")
    check(not state.records(state.answers), "textarea gesture submitted answer")


def delayed_navigation(reply, return_pending):
    def run(page, ctx, state, theme, lang, engine):
        # Hold delivery to the app, not the stand request. This works with
        # WebKit's fetch too and allows navigation without a timing race.
        ctx.add_init_script(r"""
        (() => {
          const nativeFetch = window.fetch.bind(window);
          let release;
          const held = new Promise(resolve => { release = resolve; });
          const gate = window.__ownAnswerGate = {arrived: false, completed: false, release};
          window.fetch = async (url, options) => {
            if (!String(url).endsWith('/answer') || options?.method !== 'POST')
              return nativeFetch(url, options);
            let response, error;
            try { response = await nativeFetch(url, options); } catch (e) { error = e; }
            gate.arrived = true;
            await held;
            if (error) {
              setTimeout(() => { gate.completed = true; }, 0);
              throw error;
            }
            const json = response.json.bind(response);
            response.json = async () => {
              try { return await json(); }
              finally { setTimeout(() => { gate.completed = true; }, 0); }
            };
            return response;
          };
        })();
        """)
        cookie(ctx, reply)
        other = "c3e5a7b9"
        data = deepcopy(BASE)
        data["session"][other]["question"] = deepcopy(data["session"][SID]["question"])
        data["session"][other]["question"]["question"] = "Other session question"
        state.write(data)
        root = opened(page, theme)
        root.locator(FIELD).fill(TEXT)
        root.locator(SEND).click()
        state.posted(page)
        expect(root.locator(SEND)).to_be_disabled()
        page.wait_for_function("window.__ownAnswerGate.arrived")
        old = root.element_handle()
        # Invoke the actual back handler while the busy sheet covers it.
        page.locator("#session-screen [data-back]").first.evaluate("e=>e.click()")
        expect(page.locator(".lab-ask-root")).to_have_count(0)
        expect(page.locator("#session-screen")).to_be_hidden()
        check(not old.evaluate("e=>e.isConnected"), "busy overlay survived navigation")
        page.locator(f'.card[data-id="{other}"]').click()
        root = page.locator(OPEN)
        expect(root.locator(".lab-ask-q")).to_have_text("Other session question")
        root.locator('[data-act="own"]').click()
        root.locator(FIELD).fill("other session draft")
        expect(root.locator(SEND)).to_be_enabled()
        if return_pending:
            page.locator("#session-screen [data-back]").first.evaluate("e=>e.click()")
            expect(page.locator("#session-screen")).to_be_hidden()
            page.locator(f'.card[data-id="{SID}"]').click()
            root = page.locator(OPEN)
            expect(root).to_be_visible()
            own = root.locator('[data-act="own"]')
            option = root.locator(".lab-ask-opt").first
            expect(own).to_be_disabled()
            expect(option).to_be_disabled()
            own.evaluate("e=>e.click()")
            option.evaluate("e=>e.click()")
            expect(root.locator(FIELD)).to_be_hidden()
            state.posted(page)
        else:
            root.locator(FIELD).focus()
            current_root = root.element_handle()
        page.evaluate("window.__ownAnswerGate.release()")
        page.wait_for_function("window.__ownAnswerGate.completed")
        if not return_pending:
            check(current_root.evaluate("e=>e.isConnected"), "late reply replaced other session sheet")
            expect(root.locator(".lab-ask-q")).to_have_text("Other session question")
            expect(root.locator(FIELD)).to_have_value("other session draft")
            expect(root.locator(FIELD)).to_be_focused()
            expect(root.locator(SEND)).to_be_enabled()
            expect(root.locator(".lab-ask-own-error")).to_be_hidden()
            expect(page.locator("#toast")).to_be_hidden()
            page.locator("#session-screen [data-back]").first.evaluate("e=>e.click()")
            expect(page.locator("#session-screen")).to_be_hidden()
            page.locator(f'.card[data-id="{SID}"]').click()
        root = page.locator(OPEN)
        if reply == "ok":
            expect(page.locator("#session-screen")).to_be_visible()
            expect(page.locator(".lab-ask-root")).to_have_count(0)
            state.poll(page)
            expect(page.locator(".lab-ask-root")).to_have_count(0)
        elif reply == "next":
            expect(root.locator(".lab-ask-q")).to_have_text(NEXT)
            expect(root.locator('[data-act="own"]')).to_be_enabled()
            expect(root.locator(".lab-ask-opt").first).to_be_enabled()
            root.locator('[data-act="own"]').click()
            expect(root.locator(FIELD)).to_have_value("")
            expect(root.locator(".lab-ask-own-error")).to_be_hidden()
        else:
            expect(root.locator(FIELD)).to_be_visible()
            expect(root.locator(FIELD)).to_have_value(TEXT)
            expect(root.locator(FIELD)).to_have_js_property("readOnly", False)
            expect(root.locator(".lab-ask-own-error")).to_be_visible()
            expect(root.locator(SEND)).to_be_enabled()
            state.poll(page)
            expect(root.locator(FIELD)).to_have_value(TEXT)
            expect(root.locator(SEND)).to_be_enabled()
        state.posted(page)
        if reply in ("fail", "network"):
            # Clear the receipt only after proving exactly one first request;
            # a successful retry must send exactly once and retire the editor.
            state.answers.write_text("")
            cookie(ctx, "ok")
            root.locator(SEND).click()
            state.posted(page)
            expect(page.locator(".lab-ask-root")).to_have_count(0)
    return run


def composer(page, ctx, state, theme, lang, engine):
    sid = "c3e5a7b9"
    page.goto(URL + "/app/?theme=tg-" + theme, wait_until="load")
    page.locator(f'.card[data-id="{sid}"]').click()
    expect(page.locator("#session-screen")).to_be_visible()
    expect(page.locator(".lab-ask-root")).to_have_count(0)
    text = "ordinary composer regression"
    page.locator("#s-text").fill(text)
    page.locator("#s-send").click()
    state.wait(page, lambda: bool(state.records(state.sends)), "ordinary composer /send missing")
    records = state.records(state.sends)
    check(len(records) == 1 and records[0]["sid"] == sid and
          json.loads(records[0]["body"]) == {"text": text}, "wrong ordinary composer receipt")
    check(not state.records(state.answers), "ordinary composer sent /answer")
    expect(page.locator("#s-text")).to_have_value("")


def main():
    count = failures = 0
    engines = [name.strip() for name in os.environ.get("BROWSERS", "chromium,webkit").split(",") if name.strip()]
    check(bool(engines) and all(name in ("chromium", "webkit") for name in engines), "invalid BROWSERS")
    with tempfile.TemporaryDirectory(prefix="cc-own-") as tmp:
        state = State(Path(tmp))
        state.reset()
        env = dict(os.environ, DATA_FILE=str(state.data), ANSWER_LOG=str(state.answers), SEND_LOG=str(state.sends), GET_LOG=str(state.gets), PORT=str(PORT))
        env.pop("FAIL_SESSIONS", None)
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
                    browser = getattr(pw, engine).launch()
                    try:
                        for theme in ("light", "dark"):
                            for lang in ("en", "ru"):
                                cases = [("editor and poll", editor), ("cancel and Escape", cancel_escape), ("keyboard long heading and error", keyboard), ("textarea gesture", textarea_gesture), ("ordinary composer", composer)]
                                cases += [(f"late {reply} return_{when}", delayed_navigation(reply, when == "pending"))
                                          for reply in ("ok", "next", "fail", "network")
                                          for when in ("pending", "settled")]
                                cases += [("reply=" + reply, response(reply)) for reply in ("ok", "next", "fail", "network")]
                                for label, test in cases:
                                    state.reset()
                                    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ru-RU" if lang == "ru" else "en-US", color_scheme=theme, reduced_motion=os.environ.get("REDUCED_MOTION", "no-preference"))
                                    ctx.add_init_script("localStorage.setItem('cc-lang'," + json.dumps(lang) + ")")
                                    ctx.route("https://telegram.org/**", lambda route: route.fulfill(body=""))
                                    page = ctx.new_page()
                                    errors = []
                                    page.on("pageerror", lambda error: errors.append(str(error)))
                                    count += 1
                                    try:
                                        test(page, ctx, state, theme, lang, engine)
                                        if test is not composer:
                                            check(not state.records(state.sends), "own answer incorrectly POSTed to /send: " + state.sends.read_text())
                                        check(not errors, f"page errors: {errors}")
                                        print(f"PASS {engine}/{theme}/{lang} {label}", flush=True)
                                    except Exception as error:
                                        failures += 1
                                        message = str(error) if os.environ.get("VERBOSE") else str(error).splitlines()[0]
                                        print(f"FAIL {engine}/{theme}/{lang} {label}: {message}", flush=True)
                                    finally:
                                        ctx.close()
                    finally:
                        browser.close()
        finally:
            server.terminate()
            server.wait(timeout=5)
    print(f"TOTAL {count - failures} PASS, {failures} FAIL", flush=True)
    return int(bool(failures))


if __name__ == "__main__":
    try:
        result = main()
    except Exception as error:
        print(f"FAIL INFRA {type(error).__name__}: {error}", flush=True)
        print("TOTAL BLOCKED: browser checks not completed", flush=True)
        result = 2
    sys.exit(result)
