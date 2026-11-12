"""Provider -> model picker (08.11 owner: «выбор модели уебанский», «почему нет выбора модели в экране нового чата?»).
Bot card: one collapsed row «provider · model», rows open in place, models of a known family as rows, Codex keeps a model field.
New session: the model row sends {model, sdk}. RU/EN x light/dark at 390x844, targets >= 44, no horizontal overflow.
Run: python3 tests/webapp/check_model_pick.py   (own stand on PORT, default 18989; BROWSER=chromium|webkit)
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "18989"))
URL = f"http://127.0.0.1:{PORT}"


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def fits(page, sel):
    for b in page.locator(sel).all():
        if b.is_visible():
            r = b.bounding_box(); check(r["height"] >= 44 and r["x"] >= 0 and r["x"] + r["width"] <= 391, f"target {sel} {r}")
    check(page.evaluate("document.documentElement.scrollWidth") <= 390, "horizontal overflow")


def shot(page, name):  # SHOTS=<dir>: screenshots for an eye check
    if os.environ.get("SHOTS"):
        theme = "dark" if page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches") else "light"
        page.screenshot(path=os.path.join(os.environ["SHOTS"], f"{name}-{theme}.png"), full_page=True)


def bot_card(page, lang):
    urllib.request.urlopen(urllib.request.Request(URL + "/api/bots/reset", data=b"{}", method="POST")).read()
    page.goto(f"{URL}/app/", wait_until="load")
    page.locator("#list-more").click(); page.locator('.mn-row[data-k="bots"]').click(); page.locator("#b-list .brow").nth(0).click()
    prov = page.locator("#b-prov")
    expect(prov.locator(".pv-now")).to_contain_text("Claude")
    expect(prov.locator(".pv-now")).to_contain_text("claude-sonnet-4-5")
    expect(prov.locator(".pv-r")).to_have_count(0)  # collapsed: no rows of providers until a tap
    expect(page.locator("#b-models .pv-r")).to_have_count(5)
    expect(page.locator('#b-models [data-m="fable"]')).to_contain_text("Fable 5.2")
    expect(page.locator('#b-models [data-m="claude-fable-5-1"]')).to_contain_text("Fable 5.1")
    page.locator('#b-models [data-m="opus"]').click()
    expect(page.locator("#b-model")).to_have_value("opus")
    expect(page.locator('#b-models [data-m="opus"]')).to_have_attribute("aria-checked", "true")
    expect(prov.locator(".pv-now")).to_contain_text("Opus 5.5")
    shot(page, f"bot-{lang}")
    prov.locator(".pv-now").click()
    expect(prov.locator(".pv-now")).to_have_attribute("aria-expanded", "true")
    expect(prov.locator(".pv-r")).to_have_count(6)  # Claude, Kimi, Codex, Grok, Gemini, own address
    expect(prov.locator('[data-p="anthropic"]')).to_have_attribute("aria-checked", "true")
    fits(page, "#b-prov button, #b-models button")
    prov.locator('[data-p="codex"]').click()
    expect(prov.locator(".pv-r")).to_have_count(0)
    expect(prov.locator(".pv-now")).to_contain_text("Codex")
    expect(page.locator("#b-model")).to_be_visible(); expect(page.locator("#b-key")).to_be_hidden(); expect(page.locator("#b-models")).to_be_hidden()
    page.locator("#b-model").fill("gpt-5.5")
    expect(prov.locator(".pv-now")).to_contain_text("gpt-5.5")
    with page.expect_request(lambda r: r.url.endswith("/api/bots/save")) as rq:
        page.locator("#b-save").click()
    body = json.loads(rq.value.post_data)
    check(body["provider"] == "codex" and body["model"] == "gpt-5.5" and "key" not in body, f"codex save body {body}")


def new_chat(page, lang):
    page.goto(f"{URL}/app/", wait_until="load")
    page.locator("#new-btn").click()
    nm = page.locator("#n-model")
    expect(nm.locator(".pv-now")).to_contain_text("По умолчанию" if lang == "ru" else "Default")
    nm.locator(".pv-now").click()
    expect(nm.locator(".pv-r")).to_have_count(7)  # default + fable/opus/sonnet/haiku/fable-5-1 + the SDK provider's model
    expect(nm.locator('[data-p="c:fable"]')).to_contain_text("Fable 5.2")
    page.wait_for_timeout(700); shot(page, f"new-{lang}")
    fits(page, "#n-model button")
    nm.locator('[data-p="s:glm-5.3"]').click()
    expect(nm.locator(".pv-now")).to_contain_text("glm-5.3")
    page.locator("#n-text").fill("check the logs")
    with page.expect_request(lambda r: r.url.endswith("/api/new")) as rq:
        page.locator("#fab").click()
    body = json.loads(rq.value.post_data)
    check(body.get("model") == "glm-5.3" and body.get("sdk") is True and body.get("text") == "check the logs", f"new body {body}")


def main():
    server = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, PORT=str(PORT)), stdout=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try: urllib.request.urlopen(URL + "/api/bots").read(); break
            except Exception: time.sleep(.1)
        with sync_playwright() as pw:
            browser = getattr(pw, os.environ.get("BROWSER", "chromium")).launch()
            for lang in ("en", "ru"):
                for theme in ("light", "dark"):
                    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, color_scheme=theme,
                                              locale="ru-RU" if lang == "ru" else "en-US", service_workers="block")
                    page = ctx.new_page(); page.set_default_timeout(5000)
                    bot_card(page, lang); new_chat(page, lang)
                    ctx.close()
            browser.close()
    finally:
        server.terminate()
    print("check_model_pick: OK")


if __name__ == "__main__":
    main()
