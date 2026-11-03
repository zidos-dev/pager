"""Permission acceptance on synthetic API state; run externally with Playwright."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
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
TMUX_ID = "p-tmux01"
SDK_ID = "s-sdk0001"
TMUX = {
    "kind": "tmux", "req": None, "path": "", "title": "Bash command",
    "summary": "Write synthetic acceptance output into out.txt",
    "body": "\n".join(f"echo 'synthetic permission line {i:02d}' >> out.txt" for i in range(1, 31)),
    "note": "Claude requested permissions to edit a synthetic sensitive file.",
    "question": "Do you want to proceed?",
    "options": [
        {"n": 1, "label": "Yes"},
        {"n": 2, "label": "Yes, and always allow access to synthetic sensitive files from this project"},
        {"n": 3, "label": "Yes, and switch to auto mode · auto mode handles these prompts for you"},
        {"n": 4, "label": "No"},
    ],
    "selected": 1,
}
SDK = {
    "kind": "sdk", "req": "r-synthetic-sdk-001", "title": "Bash",
    "summary": "", "body": "ls -la", "path": "/synthetic/a/b.txt",
    "note": "", "question": "",
    "options": [
        {"n": 1, "label": "Разрешить"},
        {"n": 2, "label": "Всегда Bash в этой сессии"},
        {"n": 3, "label": "Отклонить"},
    ],
    "selected": 1,
}
NEXT_BODY = "echo 'next permission request' >> next.txt"
STALE_BODY = "echo 'fresh permission request after stale answer' >> fresh.txt"
BASE = json.loads(SOURCE.read_text())
for sid, perm in ((TMUX_ID, TMUX), (SDK_ID, SDK)):
    row = deepcopy(BASE["sessions"]["sessions"][0])
    row.update(id=sid, name="Synthetic " + perm["kind"] + " permission", base_name="Synthetic " + perm["kind"] + " permission", perm=True, ask=None, detail="Raw synthetic detail must be replaced")
    BASE["sessions"]["sessions"].insert(0, row)
    session = dict(row, history=[], cwd="/synthetic", question=None, perm=deepcopy(perm))
    for reply, body in (("next", NEXT_BODY), ("stale", STALE_BODY)):
        fresh = deepcopy(perm)
        fresh.update(body=body, summary="Synthetic " + reply + " summary")
        if perm["kind"] == "sdk":
            fresh["req"] = "r-synthetic-sdk-" + reply
        session["_perm_" + reply] = fresh
    BASE["session"][sid] = session


def check(ok, message):
    if not ok:
        raise AssertionError(message)


class State:
    def __init__(self, datafile, logfile, getlog):
        self.datafile = datafile
        self.logfile = logfile
        self.getlog = getlog

    def reset(self, data=None):
        self.write(data if data is not None else BASE)
        self.logfile.write_text("")
        self.getlog.write_text("")

    def write(self, data):
        data = deepcopy(data)
        data["_test_case"] = time.monotonic_ns()
        temporary = self.datafile.with_suffix(".reset")
        temporary.write_text(json.dumps(data, ensure_ascii=False))
        temporary.replace(self.datafile)

    def records(self):
        return [json.loads(line) for line in self.logfile.read_text().splitlines() if line]

    def posted(self, page):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            records = self.records()
            if records:
                return records
            page.wait_for_timeout(25)
        raise AssertionError("permission POST missing from PERM_LOG")

    def delayed_get(self, page, phase):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            records = [json.loads(line) for line in self.getlog.read_text().splitlines() if line]
            if any(row["sid"] == TMUX_ID and row["delay"] > 0 and row["phase"] == phase for row in records):
                return
            page.wait_for_timeout(50)
        raise AssertionError("delayed session GET not " + phase)


def ready(page, theme=None):
    page.goto(URL + "/app/" + ("?theme=tg-" + theme if theme else ""), wait_until="load")
    expect(page.locator(f'.card[data-id="{TMUX_ID}"]')).to_be_visible()


def opened(page, sid=TMUX_ID, theme=None):
    ready(page, theme)
    page.locator(f'.card[data-id="{sid}"]').click()
    root = page.locator('.lab-ask-root.permission[data-state="open"]')
    expect(root).to_be_visible()
    expect(page.locator("#s-question .perm-card")).to_have_count(1)
    # Даём пружине закончить движение перед геометрией и снимками.
    page.wait_for_timeout(1000)
    return root


def reply_cookie(ctx, reply, delay=None):
    cookies = [{"name": "perm_reply", "value": reply, "url": URL}]
    if delay is not None:
        cookies.append({"name": "perm_delay", "value": str(delay), "url": URL})
    ctx.add_cookies(cookies)


def options(root):
    return root.locator(".lab-ask-opt")


def post_equals(state, page, sid, body):
    records = state.posted(page)
    check(len(records) == 1, f"expected one permission POST, got {records}")
    check(records[0]["sid"] == sid and records[0]["body"] == body, f"incorrect POST: {records[0]}")


def permission_geometry(page, root):
    viewport = page.viewport_size
    sheet = root.locator(".lab-ask-sheet")
    box = sheet.bounding_box()
    check(box is not None and box["y"] >= 24 - 1
          and box["y"] + box["height"] <= viewport["height"] + 1
          and box["x"] >= -1 and box["x"] + box["width"] <= viewport["width"] + 1,
          f"sheet exceeds viewport/safe gap {viewport}: {box}")
    rows = options(root)
    expect(rows).to_have_count(4)
    for i, row in enumerate(rows.all()):
        button = row.bounding_box()
        check(button is not None and button["y"] >= box["y"] - 1
              and button["y"] + button["height"] <= min(viewport["height"], box["y"] + box["height"]) + 1
              and button["x"] >= -1 and button["x"] + button["width"] <= viewport["width"] + 1,
              f"option {i + 1} outside sheet/viewport {viewport}: {button}, sheet={box}")
        check(button["height"] >= 44, f"option {i + 1} target below 44px: {button}")
    for selector in (".lab-ask-sheet", ".lab-perm-content", ".lab-ask-list"):
        scroll = root.locator(selector).evaluate("e=>({height:e.clientHeight,scroll:e.scrollHeight,top:e.scrollTop})")
        check(scroll["scroll"] <= scroll["height"] + 1 and scroll["top"] == 0,
              f"{selector} needs outer scrolling {viewport}: {scroll}")
    body = root.locator(".lab-perm-body")
    css = body.evaluate("""e=>{const s=getComputedStyle(e);return {
      height:e.clientHeight,scroll:e.scrollHeight,overflow:s.overflowY,overscroll:s.overscrollBehaviorY};}""")
    check(0 < css["height"] <= viewport["height"] * .43, f"body height {viewport}: {css}")
    check(css["scroll"] > css["height"] and css["overflow"] in ("auto", "scroll")
          and css["overscroll"] == "contain", f"body has no contained inner scroll {viewport}: {css}")
    check(body.evaluate("e=>{e.scrollTop=40;return e.scrollTop;}") > 0, "body cannot scroll")
    body.evaluate("e=>e.scrollTop=0")


def compact_details(page, ctx, state):
    page.set_viewport_size({"width": 390, "height": 667})
    permission_geometry(page, opened(page))


def selection_tone(page, ctx, state):
    root = opened(page)
    selected = root.locator(".perm-selected")
    expect(selected).to_have_count(1)
    check(not selected.evaluate("e=>e.matches(':focus-visible')"), "selected option unexpectedly has keyboard focus")
    css = selected.evaluate("""e=>{const s=getComputedStyle(e);return {
      outline:s.outlineStyle,width:parseFloat(s.outlineWidth),
      borders:[s.borderTopWidth,s.borderRightWidth,s.borderBottomWidth,s.borderLeftWidth].map(parseFloat),
      background:s.backgroundColor};}""")
    check((css["outline"] == "none" or css["width"] == 0) and all(v == 0 for v in css["borders"]),
          f"default selection has a frame: {css}")
    ordinary = root.locator(".lab-ask-opt:not(.perm-selected):not(.perm-deny)").first
    background = ordinary.evaluate("e=>getComputedStyle(e).backgroundColor")
    check(css["background"] != background, f"selected background equals ordinary: {css}")
    selected.focus()
    page.keyboard.press("Tab")
    page.keyboard.press("Shift+Tab")
    expect(selected).to_be_focused()
    check(selected.evaluate("e=>{const s=getComputedStyle(e);return e.matches(':focus-visible')&&s.outlineStyle!=='none'&&parseFloat(s.outlineWidth)>0;}"),
          "keyboard focus has no visible ring")
    check(not state.records(), "selection/focus submitted an answer")


def details(page, ctx, state):
    root = opened(page)
    permission_geometry(page, root)
    expect(root.locator(".lab-ask-q")).to_have_text(TMUX["title"])
    for field in ("summary", "body", "note", "question"):
        expect(root.locator(".lab-perm-" + field)).to_be_visible()
        expect(root.locator(".lab-perm-" + field)).to_have_text(TMUX[field])
    body = root.locator(".lab-perm-body")
    css = body.evaluate("""e=>{const s=getComputedStyle(e);return {
      font:s.fontFamily,white:s.whiteSpace,wrap:s.overflowWrap,overflow:s.overflowY,
      height:e.clientHeight,scroll:e.scrollHeight,width:e.clientWidth,scrollWidth:e.scrollWidth};}""")
    check(any(font in css["font"].lower() for font in ("mono", "consolas", "courier")), f"body is not monospace: {css}")
    check(css["white"] == "pre-wrap" and css["wrap"] == "anywhere", f"body wrapping: {css}")
    check(css["height"] <= 844 * .43, f"body exceeds ~40% viewport: {css}")
    check(css["scroll"] > css["height"] and css["overflow"] in ("auto", "scroll"), f"long body has no inner scroll: {css}")
    check(css["scrollWidth"] <= css["width"] + 1, "body overflows horizontally")
    sheet = root.locator(".lab-ask-sheet").bounding_box()
    check(sheet["y"] >= -1 and sheet["y"] + sheet["height"] <= 845, f"sheet exceeds viewport: {sheet}")
    rows = options(root)
    expect(rows).to_have_count(4)
    boxes = [rows.nth(i).bounding_box() for i in range(4)]
    check(all(box["height"] >= 44 for box in boxes), f"button targets below 44px: {boxes}")
    expect(rows.last).to_contain_text("No")
    expect(rows.last).to_have_class(re.compile(r"\bperm-deny\b"))
    ordinary_gap = boxes[1]["y"] - boxes[0]["y"] - boxes[0]["height"]
    deny_gap = boxes[3]["y"] - boxes[2]["y"] - boxes[2]["height"]
    check(deny_gap >= ordinary_gap + 8 - .5, f"deny gap={deny_gap}, ordinary={ordinary_gap}")
    expect(rows.first).to_have_class(re.compile(r"\bperm-selected\b"))
    check(not state.records(), "default selected option was submitted automatically")
    for index in (1, 2):
        button = rows.nth(index)
        short = button.locator(".lab-ask-t").inner_text()
        full = TMUX["options"][index]["label"]
        check(short != full and len(short) <= 49 and " · " not in short, f"label not shortened: {short!r}")
        expect(button).to_have_attribute("aria-label", full)
        expect(button).to_have_attribute("title", full)


def tmux_post(page, ctx, state):
    reply_cookie(ctx, "null", 1.5)
    root = opened(page)
    options(root).nth(1).click()
    state.posted(page)
    expect(page.locator('.lab-ask-root.permission[data-state="open"]')).to_be_visible()
    for button in page.locator(".lab-ask-root.permission .lab-ask-opt").all():
        expect(button).to_be_disabled()
    page.keyboard.press("Escape")
    expect(page.locator('.lab-ask-root.permission[data-state="open"]')).to_be_visible()
    post_equals(state, page, TMUX_ID, {"option": 2, "label": TMUX["options"][1]["label"]})
    expect(page.locator(".lab-ask-root.permission")).to_have_count(0)
    expect(page.locator("#toast")).to_be_visible()
    expect(page.locator("#toast")).to_have_text("Sent")


def long_press(page, ctx, state):
    root = opened(page)
    button = options(root).nth(1)
    button.scroll_into_view_if_needed()
    box = button.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(600)
    expect(page.locator("#toast")).to_have_text(TMUX["options"][1]["label"])
    page.mouse.up()
    check(not state.records(), "long press submitted permission answer")
    expect(page.locator('.lab-ask-root.permission[data-state="open"]')).to_be_visible()
    button.click()
    post_equals(state, page, TMUX_ID, {"option": 2, "label": TMUX["options"][1]["label"]})
    expect(page.locator(".lab-ask-root.permission")).to_have_count(0)


def priority(page, ctx, state):
    data = deepcopy(BASE)
    data["session"][TMUX_ID]["question"] = deepcopy(BASE["session"]["a1c3e5f7"]["question"])
    state.write(data)
    root = opened(page)
    expect(options(root)).to_have_count(4)
    options(root).first.click()
    expect(page.locator(".lab-ask-root.permission")).to_have_count(0)
    question = page.locator('.lab-ask-root:not(.permission)[data-state="open"]')
    expect(question).to_be_visible()
    expect(options(question)).to_have_count(3)


def delayed_navigation(page, ctx, state):
    opened(page)
    # Старый GET читает tmux до задержки; SDK GET не получает этот cookie.
    ctx.add_cookies([{"name": "session_delay", "value": "1.5", "domain": "127.0.0.1", "path": "/api/session/" + TMUX_ID}])
    state.delayed_get(page, "captured")
    page.keyboard.press("Escape")
    expect(page.locator('.lab-ask-root.permission[data-state="pill"]')).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#session-screen")).to_be_hidden()
    page.locator(f'.card[data-id="{SDK_ID}"]').click()
    expect(page.locator(".lab-ask-root.permission .lab-ask-q")).to_have_text(SDK["title"])
    state.delayed_get(page, "completed")
    page.wait_for_timeout(300)
    expect(page.locator(".lab-ask-root.permission .lab-ask-q")).to_have_text(SDK["title"])
    expect(page.locator("#s-question .perm-card")).to_have_attribute("data-session", SDK_ID)
    check(not state.records(), "navigation submitted a permission answer")


def response_case(reply):
    def run(page, ctx, state):
        reply_cookie(ctx, reply)
        root = opened(page)
        options(root).first.click()
        post_equals(state, page, TMUX_ID, {"option": 1, "label": "Yes"})
        if reply in ("next", "stale"):
            expect(page.locator(".lab-ask-root.permission .lab-perm-body")).to_have_text(NEXT_BODY if reply == "next" else STALE_BODY)
            expect(page.locator('.lab-ask-root.permission[data-state="open"]')).to_be_visible()
            # После loadSession свежий запрос остаётся, кнопки снова доступны.
            expect(page.locator(".lab-ask-root.permission .lab-ask-opt").first).to_be_enabled()
        elif reply in ("null", "closed", "already"):
            expect(page.locator(".lab-ask-root.permission")).to_have_count(0)
            expect(page.locator("#toast")).to_be_visible()
            expect(page.locator("#toast")).to_have_text("Request already closed" if reply == "closed" else "Sent")
            check(json.loads(state.datafile.read_text())["session"][TMUX_ID]["perm"] is None, "stand did not close permission")
        else:
            expect(page.locator('.lab-ask-root.permission[data-state="open"]')).to_be_visible()
            expect(page.locator("#toast")).to_be_visible()
            if reply in ("error", "stopped"):
                expect(page.locator("#toast")).to_contain_text("Synthetic permission " + reply)
            else:
                check(bool(page.locator("#toast").inner_text()), f"{reply}: no error text")
            expect(page.locator(".lab-ask-root.permission .lab-perm-body")).to_have_text(TMUX["body"])
            expect(page.locator(".lab-ask-root.permission .lab-ask-opt").first).to_be_enabled()
    return run


def sdk_post(page, ctx, state):
    root = opened(page, SDK_ID)
    expect(options(root)).to_have_count(3)
    expect(root.locator(".lab-perm-path")).to_have_text(SDK["path"])
    expect(root.locator(".lab-perm-body")).to_have_text(SDK["body"])
    expect(options(root).last).to_contain_text("Отклонить")
    options(root).first.click()
    post_equals(state, page, SDK_ID, {"option": 1, "req": SDK["req"]})
    expect(page.locator(".lab-ask-root.permission")).to_have_count(0)


def escape(page, ctx, state):
    root = opened(page)
    options(root).first.focus()
    page.keyboard.press("Escape")
    expect(page.locator('.lab-ask-root.permission[data-state="pill"]')).to_be_visible()
    expect(page.locator(".lab-ask-ptx")).to_have_text("Permission · Bash command")
    expect(page.locator("#session-screen")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#session-screen")).to_be_hidden()
    check(not state.records(), "Escape submitted a permission answer")


def reopen_after_collapse(sid, outside=False):
    def run(page, ctx, state):
        if sid == TMUX_ID:
            root = opened(page)
        else:
            ready(page)
            page.locator(f'.card[data-id="{sid}"]').click()
            root = page.locator('.lab-ask-root[data-state="open"]')
            expect(root).to_be_visible()
            page.wait_for_timeout(1000)
        root = page.locator(".lab-ask-root")
        if outside:
            root.locator(".lab-ask-scrim").click(position={"x": 12, "y": 12})
        else:
            page.keyboard.press("Escape")
        expect(page.locator('.lab-ask-root[data-state="pill"]')).to_have_count(1)
        check(root.locator(".lab-ask-scrim").evaluate("e=>getComputedStyle(e).pointerEvents") == "none",
              "scrim still intercepts taps at collapse start")
        page.wait_for_timeout(200)
        pill = root.locator(".lab-ask-pill")
        hit = pill.evaluate("""e=>{const r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}""")
        check(hit, "pill is covered 200ms after collapse")
        pill.click(timeout=1000)
        expect(page.locator('.lab-ask-root[data-state="open"]')).to_have_count(1)
        check(not state.records(), "reopening submitted a permission answer")
    return run


def list_status(page, ctx, state):
    ready(page)
    card = page.locator(f'.card[data-id="{TMUX_ID}"]')
    expect(card.locator(".ds")).to_have_text("Needs permission")
    expect(card.locator("svg.pi")).to_have_count(1)  # строка разрешения: замок вместо точки (запрос владельца 15.10 12:52)


def ordinary_question(page, ctx, state):
    ready(page)
    page.locator('.card[data-id="a1c3e5f7"]').click()
    root = page.locator('.lab-ask-root[data-state="open"]')
    expect(root).to_be_visible()
    check("permission" not in (root.get_attribute("class") or "").split(), "ordinary question became permission")
    expect(options(root)).to_have_count(3)
    options(root).first.click()
    expect(page.locator('.lab-ask-root[data-state="open"]')).to_have_count(0)
    check(not state.records(), "ordinary question sent a permission POST")


def russian(page, ctx, state):
    ready(page)
    expect(page.locator(f'.card[data-id="{TMUX_ID}"] .ds')).to_have_text("Нужно разрешение")
    page.locator(f'.card[data-id="{TMUX_ID}"]').click()
    expect(page.locator('.lab-ask-root.permission[data-state="open"]')).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator('.lab-ask-root.permission[data-state="pill"]')).to_have_count(1)
    expect(page.locator(".lab-ask-ptx")).to_have_text("Разрешение · Bash command")
    page.locator(".lab-ask-pill").click()
    page.locator(".lab-ask-root.permission .lab-ask-opt").first.click()
    expect(page.locator("#toast")).to_have_text("Ответ отправлен")
    expect(page.locator(".lab-ask-root.permission")).to_have_count(0)


def screenshot_case(kind, theme, engine, height=844):
    def run(page, ctx, state):
        page.set_viewport_size({"width": 390, "height": height})
        page.emulate_media(color_scheme=theme)
        root = opened(page, TMUX_ID if kind == "tmux" else SDK_ID, theme)
        expect(page.locator("html")).to_have_attribute("data-lab-theme", theme)
        expect(root.locator(".lab-ask-q")).to_have_text(TMUX["title"] if kind == "tmux" else SDK["title"])
        if kind == "tmux":
            permission_geometry(page, root)
        suffix = "-390x667" if height == 667 else ""
        page.screenshot(path=str(ROOT / f"perm-{engine}-{kind}-{theme}{suffix}.png"), full_page=False)
    return run


def main():
    failures = count = 0
    engines = [name.strip() for name in os.environ.get("BROWSERS", "chromium,webkit").split(",") if name.strip()]
    check(bool(engines) and all(name in ("chromium", "webkit") for name in engines), "BROWSERS must select chromium and/or webkit")
    reduced = os.environ.get("REDUCED_MOTION", "no-preference")
    with tempfile.TemporaryDirectory(prefix="cc-perm-") as tmp:
        state = State(Path(tmp) / "demo.json", Path(tmp) / "perm.jsonl", Path(tmp) / "gets.jsonl")
        state.reset()
        env = dict(os.environ, DATA_FILE=str(state.datafile), PERM_LOG=str(state.logfile), GET_LOG=str(state.getlog), PORT=str(PORT))
        for key in ("FAIL_SESSIONS", "perm_reply", "PERM_REPLY", "PERM_DELAY"):
            env.pop(key, None)
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
            else:
                raise RuntimeError("test server readiness timeout")
            with sync_playwright() as pw:
                for engine in engines:
                    browser = getattr(pw, engine).launch()
                    try:
                        cases = [("details, sizing, long labels, selection", details), ("tmux full-label POST and pending state", tmux_post), ("long press shows full label without submitting", long_press), ("permission before ordinary question", priority), ("delayed GET cannot replace another session", delayed_navigation)]
                        cases += [("390x667 body scroll and all options inside viewport", compact_details), ("default selection tone and keyboard focus ring", selection_tone)]
                        cases += [(f"{sid} {trigger} then pill at 200ms", reopen_after_collapse(sid, trigger == "outside"))
                                  for sid in (TMUX_ID, "a1c3e5f7") for trigger in ("Escape", "outside")]
                        cases += [("reply=" + reply, response_case(reply)) for reply in ("next", "null", "closed", "stale", "error", "already", "stopped", "bad", "network")]
                        cases += [("SDK req POST and path", sdk_post), ("Escape collapses then exits", escape), ("list permission status", list_status), ("ordinary question regression", ordinary_question), ("Russian list, pill and sent toast", russian)]
                        cases += [(f"screenshot {kind}/{theme}", screenshot_case(kind, theme, engine)) for kind in ("tmux", "sdk") for theme in ("light", "dark")]
                        cases += [("screenshot tmux/light/390x667", screenshot_case("tmux", "light", engine, 667))]
                        for label, test in cases:
                            state.reset()
                            ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="en-US", reduced_motion=reduced)
                            language = "ru" if test is russian else "en"
                            ctx.add_init_script("localStorage.setItem('cc-lang'," + json.dumps(language) + ")")
                            ctx.route("https://telegram.org/**", lambda route: route.fulfill(body=""))
                            page = ctx.new_page()
                            errors = []
                            page.on("pageerror", lambda error: errors.append(str(error)))
                            count += 1
                            try:
                                test(page, ctx, state)
                                page.wait_for_timeout(100)
                                check(not errors, f"page errors: {errors}")
                                print(f"PASS {engine} {label} ({reduced})", flush=True)
                            except Exception as error:
                                failures += 1
                                message = str(error) if os.environ.get("VERBOSE") else str(error).splitlines()[0]
                                print(f"FAIL {engine} {label}: {message}", flush=True)
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
