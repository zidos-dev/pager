"""Multi-question AskUserQuestion: the sheet stays up between steps (question 1 → 2 → review).
Owner 19.10 23:52 «дёргается»: the sheet dropped and rose again on each step, and when the reply came back
within 190 ms the next question's sheet was dismissed by the previous tap. Every frame is sampled: no root may
turn «gone» and the scrim must stay ≥ .9 until «Submit answers»; the next step's options stand still at once
(no per-option entrance); a failed answer leaves the sheet usable.

Run externally with Playwright; APP_DIR selects checkout. Reuses State from check_own_answer.
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

from check_own_answer import BASE, PORT, URL, SID, NEXT, OPEN, State, check, cookie

ROOT = Path(__file__).resolve().parent
DATA = deepcopy(BASE)
for q in [DATA["session"][SID]["question"]] + [r["question"] for r in DATA["sessions"]["sessions"] if r["id"] == SID and r.get("question")]:
    q.update(n=2, step=0)
SAMPLER = """window.__steps=[];(function f(){document.querySelectorAll('.lab-ask-root').forEach(r=>{const s=r.querySelector('.lab-ask-scrim');
  window.__steps.push({st:r.dataset.state,sc:s?+getComputedStyle(s).opacity:1})});requestAnimationFrame(f)})()"""


def steady(page, what):
    frames = page.evaluate("window.__steps.splice(0)")
    check(frames, what + ": no frames sampled")
    bad = [f for f in frames if f["st"] != "open" or f["sc"] < .9]
    check(not bad, f"{what}: sheet left between steps ({len(bad)}/{len(frames)} frames, first {bad[:1]})")


def steps(page, ctx, state, delay):
    cookie(ctx, "next", delay)
    page.goto(URL + "/app/?theme=tg-light", wait_until="load")
    page.locator(f'.card[data-id="{SID}"]').click()
    root = page.locator(OPEN)
    expect(root).to_be_visible()
    page.wait_for_timeout(1000)
    page.evaluate(SAMPLER)
    root.locator(".lab-ask-opt").first.click()
    expect(page.locator(OPEN).locator(".lab-ask-q")).to_have_text(NEXT, timeout=3000)
    page.wait_for_timeout(250)
    # live Telegram 20.10 04:25: the sheet grew empty and the options slid in one by one ~470 ms later («прыгает»)
    late = page.locator(OPEN).locator(".lab-ask-opt").evaluate_all(
        "os=>os.map(o=>{const c=getComputedStyle(o);return [+c.opacity,c.transform]}).filter(([op,tr])=>op<.99||(tr!=='none'&&tr!=='matrix(1, 0, 0, 1, 0, 0)'))")
    check(not late, f"question 2 options still entering 250 ms after the step: {late}")
    page.wait_for_timeout(350)  # no poll: the answer reply alone must keep question 2 up
    steady(page, f"question 1→2 (delay {delay})")
    data = json.loads(state.data.read_text())
    question = data["session"][SID]["question"]
    question.update(review=True, options=[], question="", answers=["Where should the session cache live? → Redis", NEXT + " → 1 hour"])
    for row in data["sessions"]["sessions"]:
        if row["id"] == SID:
            row["question"] = dict(question)
    state.write(data)
    state.poll(page)
    expect(page.locator(OPEN).locator('.lab-ask-btn[data-act="submit"]')).to_be_visible()
    page.wait_for_timeout(600)
    steady(page, f"question 2→review (delay {delay})")


def failed(page, ctx, state):
    cookie(ctx, "fail")
    page.goto(URL + "/app/?theme=tg-light", wait_until="load")
    page.locator(f'.card[data-id="{SID}"]').click()
    root = page.locator(OPEN)
    expect(root).to_be_visible()
    page.wait_for_timeout(1000)
    root.locator(".lab-ask-opt").first.click()
    state.wait(page, lambda: bool(state.records(state.answers)), "option POST missing")
    page.wait_for_timeout(800)
    expect(root).to_be_visible()
    check("busy" not in root.get_attribute("class").split(), "sheet stays busy after a failed answer")
    expect(root.locator(".lab-ask-opt.picked")).to_have_count(0)


def main():
    failures = 0
    engines = [n.strip() for n in os.environ.get("BROWSERS", "chromium,webkit").split(",") if n.strip()]
    with tempfile.TemporaryDirectory(prefix="cc-steps-") as tmp:
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
                for engine, case in [(e, c) for e in engines for c in ("0", "0.6", "fail")]:
                    for path in (state.answers, state.sends, state.gets):
                        path.write_text("")
                    state.write(DATA)
                    browser = getattr(pw, engine).launch()
                    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="en-US", color_scheme="light")
                    ctx.add_init_script("localStorage.setItem('cc-lang','en');localStorage.setItem('cc-filter','all')")
                    ctx.route("https://telegram.org/**", lambda route: route.fulfill(body=""))
                    page = ctx.new_page()
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    try:
                        failed(page, ctx, state) if case == "fail" else steps(page, ctx, state, case)
                        check(not errors, "page errors: " + "; ".join(errors))
                        print(f"PASS {engine} {case}")
                    except Exception as e:
                        failures += 1
                        print(f"FAIL {engine} {case}: {e}")
                    finally:
                        browser.close()
        finally:
            server.terminate()
            server.wait(timeout=5)
    print(f"TOTAL {'PASS' if not failures else 'FAIL'} ({failures} failed)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
