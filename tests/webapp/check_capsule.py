"""Capsule acceptance + existing regressions on demo2.json.

Run with APP_DIR pointing at the candidate assets/webapp. No browser checks
are skipped on launch failure; infrastructure errors return exit code 2.
"""
from contextlib import contextmanager
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
APP = Path(os.environ.get("APP_DIR", str(Path(__file__).resolve().parents[2] / "assets/webapp"))).resolve()
BEFORE = Path(os.environ.get("BEFORE_APP_DIR", APP))
PORT = int(os.environ.get("PORT", "18987"))
URL = f"http://127.0.0.1:{PORT}"
BASE = json.loads((ROOT.parent / "stand/demo2.json").read_text())
KEYS = ("act", "wait", "work", "done", "arch")
LABELS = {
    "en": ["Active", "Waiting", "Working", "Done", "Archive"],
    "ru": ["Активные", "Ждут", "В работе", "Готово", "Архив"],
}


def check(ok, message):
    if not ok:
        raise AssertionError(message)


@contextmanager
def stand(app):
    check((app / "index.html").is_file(), f"missing app: {app}")
    with tempfile.TemporaryDirectory(prefix="cc-capsule-") as tmp:
        data = Path(tmp) / "demo.json"
        data.write_text(json.dumps(BASE))
        env = dict(os.environ, APP_DIR=str(app), DATA_FILE=str(data), PORT=str(PORT))
        env.pop("FAIL_SESSIONS", None)
        server = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")],
                                  env=env, stdout=subprocess.DEVNULL)
        try:
            for _ in range(50):
                check(server.poll() is None, "stand failed to start")
                try:
                    with urllib.request.urlopen(URL + "/api/sessions", timeout=.3) as response:
                        got = json.load(response)
                        got.get("quota", {}).pop("pinned", None)  # the stand adds header pins, as the server does
                        if got.get("topics"):  # and with topics the bot's name and each session's topic
                            got.pop("bot", None)
                            for row in got.get("sessions") or []:
                                row.pop("thread", None)
                        check(got == BASE["sessions"], "unexpected stand data")
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("stand readiness timeout")
            # Also catch a failed bind if another stand answered the request.
            time.sleep(.1)
            check(server.poll() is None, "stand exited after readiness")
            yield
        finally:
            server.terminate()
            server.wait(timeout=5)


def new_page(browser, width, height, theme, lang, motion="no-preference"):
    ctx = browser.new_context(
        viewport={"width": width, "height": height}, device_scale_factor=2,
        is_mobile=width < 768, has_touch=width < 768,
        locale="ru-RU" if lang == "ru" else "en-US",
        color_scheme=theme, reduced_motion=motion,
    )
    # Same offline Telegram SDK bypass as check_defects.py; API uses the stand.
    ctx.route("https://telegram.org/**", lambda route: route.fulfill(body=""))
    ctx.add_init_script("localStorage.setItem('cc-lang', " + json.dumps(lang) +
                        ");localStorage.setItem('cc-filter','act')")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    return ctx, page, errors


def ready(page, theme, lang):
    page.goto(URL + "/app/", wait_until="load")
    expect(page.locator("#list .card").first).to_be_visible()
    expect(page.locator("#lab-fcap")).to_be_visible()
    expect(page.locator("#lab-fcap .lf-item")).to_have_count(5)
    expect(page.locator("html")).to_have_attribute("lang", lang)
    expect(page.locator("html")).to_have_attribute("data-lab-theme", theme)
    page.wait_for_timeout(400)  # Initial chipbar collapse and numeric reveal.


def bottom(page):
    page.evaluate("window.scrollTo(0, document.scrollingElement.scrollHeight)")
    page.wait_for_timeout(100)


def clearance(page):
    bottom(page)
    last = page.locator("#list .card").last.bounding_box()
    cap = page.locator("#lab-fcap").bounding_box()
    check(last is not None and cap is not None, "missing last card or capsule")
    last_bottom = last["y"] + last["height"]
    check(last["y"] >= 0 and last_bottom <= cap["y"],
          f"last card not fully above capsule: card={last}, capsule={cap}")
    check(cap["y"] - last_bottom >= 15, f"last card gap below 16px: {cap['y'] - last_bottom}")


def geometry(page, lang, width):
    cap = page.locator("#lab-fcap")
    box = cap.bounding_box()
    check(abs(box["height"] - 60) <= 1, f"capsule height: {box}")
    layout = cap.evaluate("""e=>{
      const s=getComputedStyle(e), list=document.getElementById('list-screen');
      return {padding:parseFloat(getComputedStyle(list).paddingBottom),
        bottom:parseFloat(s.bottom),scroll:e.scrollWidth,client:e.clientWidth,
        pageWidth:document.scrollingElement.scrollWidth,column:list.getBoundingClientRect().toJSON(),
        pad:parseFloat(getComputedStyle(list).paddingLeft)+parseFloat(getComputedStyle(list).paddingRight)};
    }""")
    check(abs(layout["padding"] - box["height"] - layout["bottom"] - 16) <= 1,
          f"list padding does not reserve capsule + bottom + 16px: {layout}")
    check(layout["scroll"] <= layout["client"] and layout["pageWidth"] <= width,
          f"horizontal overflow: {layout}")
    # редизайн В: на ≥1000 капсула стоит в колонке списка, иначе — по центру окна
    centre = layout["column"]["x"] + layout["column"]["width"] / 2 if width >= 1000 else width / 2
    check(abs(box["x"] + box["width"] / 2 - centre) <= 1, "capsule off center")
    if width >= 768:
        col = layout["column"]
        inner = col["width"] - (layout["pad"] if width >= 1000 else 0)  # на ≥1000 капсула ровно по карточкам колонки
        check(box["width"] <= 720 and abs(box["width"] - inner) <= 1,
              f"capsule does not fill list column: {box}, {col}")
    else:
        check(abs(box["width"] - (width - 32)) <= 1, f"mobile capsule width: {box}")
    for key, label in zip(KEYS, LABELS[lang]):
        item = page.locator(f'#lab-fcap .lf-item[data-f="{key}"]')
        expect(item.locator(".t")).to_have_text(label)
        details = item.evaluate("""e=>{
          const t=e.querySelector('.t'), n=e.querySelector('.n'),
            ts=getComputedStyle(t), ns=getComputedStyle(n), r=e.getBoundingClientRect();
          return {item:r.toJSON(),text:t.getBoundingClientRect().toJSON(),number:n.getBoundingClientRect().toJSON(),
            scroll:t.scrollWidth,client:t.clientWidth,line:parseFloat(ts.lineHeight),
            textSize:ts.fontSize,textWeight:ts.fontWeight,numSize:ns.fontSize,numWeight:ns.fontWeight,
            numeric:ns.fontVariantNumeric};
        }""")
        r, t, n = details["item"], details["text"], details["number"]
        check(r["width"] >= 44 and r["height"] >= 44, f"{key}: small target: {r}")
        check(details["scroll"] <= details["client"] and t["height"] <= details["line"] + 1,
              f"{key}: label clipped or wrapped: {details}")
        check(n["top"] < t["top"] and n["bottom"] <= t["top"], f"{key}: number not above label")
        for child in (t, n):
            check(child["left"] >= r["left"] - 1 and child["right"] <= r["right"] + 1 and
                  child["top"] >= r["top"] and child["bottom"] <= r["bottom"],
                  f"{key}: content escapes target: {details}")
        label_size = "11px" if box["width"] < 340 else "12px"
        check((details["numSize"], details["numWeight"]) == ("20px", "650") and
              (details["textSize"], details["textWeight"]) == (label_size, "500") and
              "tabular-nums" in details["numeric"], f"{key}: typography: {details}")


def select(page, key, motion, keyboard=False):
    item = page.locator(f'#lab-fcap .lf-item[data-f="{key}"]')
    if keyboard:
        item.focus()
        page.keyboard.press("Enter")
    else:
        item.click()
    expect(item).to_have_attribute("aria-pressed", "true")
    expect(page.locator(f'#chips .chip[data-f="{key}"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator('#chips .chip[aria-pressed="true"]')).to_have_count(1)
    expect(page.locator('#lab-fcap .lf-item[aria-pressed="true"]')).to_have_count(1)
    page.wait_for_timeout(100 if motion == "reduce" else 1000)
    selected, indicator = item.bounding_box(), page.locator("#lab-fcap .lf-ind").bounding_box()
    for axis, size in (("x", "width"), ("y", "height")):
        delta = abs(selected[axis] + selected[size] / 2 - indicator[axis] - indicator[size] / 2)
        check(delta <= 2, f"{key}: indicator center on {axis} differs by {delta}px")
        check(abs(selected[size] - indicator[size]) <= 1, f"{key}: indicator does not fill target")
    # (a') Every selected label must fit inside the settled highlight.
    label = item.locator(".t").bounding_box()
    check(label is not None, f"{key}: missing selected label")
    for axis, size in (("x", "width"), ("y", "height")):
        check(label[axis] >= indicator[axis] - 1 and
              label[axis] + label[size] <= indicator[axis] + indicator[size] + 1,
              f"{key}: selected label escapes indicator: label={label}, indicator={indicator}")
    expect(item).to_have_class("lf-item lf-on")
    rows = BASE["sessions"]["sessions"]
    groups = ("wait", "work") if key == "act" else ("old", "archived") if key == "arch" else (key,)
    expected = sorted(row["id"] for row in rows if row["group"] in groups)
    actual = sorted(page.locator("#list .card").evaluate_all("es=>es.map(e=>e.dataset.id)"))
    check(actual == expected, f"{key}: filtered cards: {actual}, expected {expected}")
    expect(item.locator(".n")).to_have_text(str(len(expected)))
    if expected:
        clearance(page)
    else:
        expect(page.locator("#list .empty")).to_be_visible()


def regressions():
    for name, receipt in (("check_defects.py", "TOTAL 10 PASS, 0 FAIL"), ("regress_open.py", "TOTAL PASS")):
        env = dict(os.environ, APP_DIR=str(APP), BROWSERS="chromium,webkit",
                   REDUCED_MOTION="no-preference", TAG="capsule")
        env["PORT"] = str(PORT + 1)  # свой порт вложенным: с дефолтным параллельные прогоны делили один стенд
        env.pop("FAIL_SESSIONS", None)
        result = subprocess.run([sys.executable, str(ROOT / name)], env=env,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print(result.stdout, end="", flush=True)
        check(result.returncode == 0 and receipt in result.stdout,
              f"{name}: expected {receipt!r}, exit={result.returncode}")


def main():
    failures, cases = 0, 0
    print(f"APP_DIR={APP}", flush=True)
    with sync_playwright() as pw:
        with stand(BEFORE):
            browser = pw.chromium.launch()
            try:
                ctx, page, errors = new_page(browser, 390, 844, "light", "ru")
                try:
                    ready(page, "light", "ru")
                    bottom(page)
                    page.screenshot(path=str(ROOT / "cap-before-light-390.png"))
                    check(not errors, f"before page errors: {errors}")
                finally:
                    ctx.close()
            finally:
                browser.close()
        with stand(APP):
            for engine in ("chromium", "webkit"):
                browser = getattr(pw, engine).launch()
                try:
                    configs = [(w, h, theme, lang, motion)
                               for w, h in ((390, 844), (360, 740))
                               for theme in ("light", "dark") for lang in ("en", "ru")
                               for motion in ("no-preference", "reduce")]
                    configs += [(w, 900, "light", "ru", "no-preference") for w in (768, 1440)]
                    for width, height, theme, lang, motion in configs:
                        cases += 1
                        ident = f"{engine} {width}x{height} {theme} {lang} {motion}"
                        ctx, page, errors = new_page(browser, width, height, theme, lang, motion)
                        try:
                            ready(page, theme, lang)
                            geometry(page, lang, width)
                            # Cross the capsule both ways; archive also covers a zero counter/empty list.
                            for key in ("arch", "wait", "work", "done", "act"):
                                select(page, key, motion)
                            select(page, "wait", motion, keyboard=True)
                            select(page, "act", motion)
                            if engine == "chromium" and width < 768 and lang == "ru" and motion == "no-preference":
                                page.screenshot(path=str(ROOT / f"cap-{theme}-{width}.png"))
                            check(not errors, f"page errors: {errors}")
                            print(f"PASS {ident}", flush=True)
                        except Exception as error:
                            failures += 1
                            print(f"FAIL {ident}: {error}", flush=True)
                        finally:
                            ctx.close()
                finally:
                    browser.close()
    print(f"CAPSULE TOTAL {cases - failures} PASS, {failures} FAIL", flush=True)
    regressions()
    return int(bool(failures))


if __name__ == "__main__":
    try:
        result = main()
    except AssertionError as error:
        print(f"FAIL {error}", flush=True)
        result = 1
    except Exception as error:
        print(f"FAIL INFRA {type(error).__name__}: {error}", flush=True)
        print("TOTAL BLOCKED: browser checks not completed", flush=True)
        result = 2
    sys.exit(result)
