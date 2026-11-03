"""Review step of a multi-question AskUserQuestion («Ready to submit your answers?»): question.review=true, options=[].
The native block sits at the top of the history (far above the end), so the Clio sheet must show it: the answers
and «Submit answers» → {action:"submit"}, «Cancel» → {action:"cancel"} (owner 19.10 22:44: Submit «не срабатывает»).

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
REVIEW = dict(DATA["session"][SID]["question"], step=2, n=2, review=True, options=[], question="",
              answers=[{"header": "Cache", "question": "Where should the session cache live?", "answer": "Redis"},
                       {"header": "TTL", "question": "Keep the TTL?", "answer": None}])  # the server's shape
SHOWN = ["Cache: Redis", "TTL: —"]  # not «[object Object]» (owner 20.10 04:01)
DATA["session"][SID]["question"] = REVIEW
for row in DATA["sessions"]["sessions"]:
    if row["id"] == SID and row.get("question"):
        row["question"] = dict(REVIEW)


def review(page, state, action):
    page.goto(URL + "/app/?theme=tg-light", wait_until="load")
    page.locator(f'.card[data-id="{SID}"]').click()
    root = page.locator(OPEN)
    expect(root).to_be_visible()
    page.wait_for_timeout(1000)
    for text in SHOWN:
        expect(root.get_by_text(text)).to_be_visible()
    check("object Object" not in root.inner_text(), "answers rendered as [object Object]")
    btn = root.locator(f'.lab-ask-btn[data-act="{action}"]')
    expect(btn).to_be_visible()
    box = btn.bounding_box()
    check(box["y"] + box["height"] <= 844, f"{action} button below the window: {box}")
    btn.click()
    state.wait(page, lambda: bool(state.records(state.answers)), f"{action} POST missing")
    check(state.records(state.answers) == [{"action": action}], "unexpected ANSWER_LOG: " + state.answers.read_text())


def main():
    failures = 0
    engines = [n.strip() for n in os.environ.get("BROWSERS", "chromium,webkit").split(",") if n.strip()]
    with tempfile.TemporaryDirectory(prefix="cc-review-") as tmp:
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
                for engine, action in [(e, a) for e in engines for a in ("submit", "cancel")]:
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
                        review(page, state, action)
                        check(not errors, f"page errors: {errors}")
                        print(f"PASS {engine} review {action}", flush=True)
                    except Exception as error:
                        failures += 1
                        print(f"FAIL {engine} review {action}: {(str(error) if os.environ.get("VERBOSE") else str(error).splitlines()[0])}", flush=True)
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
