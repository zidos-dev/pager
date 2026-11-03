"""Sheet «Bots» against the stand's synthetic /api/bots* (README, "Several bots"): list, add, edit, generate, errors, delete.
RU/EN x light/dark at 390x844, no horizontal overflow, targets >= 44. SHOTS=<dir> saves screenshots.
Run: python3 tests/webapp/check_bots.py   (starts its own stand on PORT, default 18990)
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "18990"))
URL = f"http://127.0.0.1:{PORT}"
SHOTS = os.environ.get("SHOTS")
T = {"en": dict(on="working", off="stopped", bad="error", nogrp="group not chosen", saved="Saved.", token="Telegram did not accept this token.", cwd="This folder does not exist on the server.", gen="Generate"),
     "ru": dict(on="работает", off="стоит", bad="ошибка", nogrp="группа не выбрана", saved="Сохранено.", token="Telegram не принял этот токен.", cwd="Такой папки нет на сервере.", gen="Сгенерировать")}


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def flow(page, lang, theme):
    s = T[lang]
    urllib.request.urlopen(urllib.request.Request(URL + "/api/bots/reset", data=b"{}", method="POST")).read()
    page.goto(f"{URL}/app/?theme=tg-{theme}", wait_until="load")
    page.locator("#bots-btn").click()
    sheet = page.locator("#bots-sheet")
    expect(sheet).to_be_visible()
    rows = page.locator("#b-list .brow")
    expect(rows).to_have_count(2)
    check(s["on"] in rows.nth(0).inner_text() and s["bad"] in rows.nth(1).inner_text(), "statuses")
    check("Shop team" in rows.nth(0).inner_text() and s["nogrp"] in rows.nth(1).inner_text(), "meta")
    page.wait_for_timeout(500)
    if SHOTS: page.screenshot(path=f"{SHOTS}/bots-list-{lang}-{theme}-390.png")
    rows.nth(0).click()
    expect(page.locator("#b-vedit")).to_be_visible()
    check(page.locator("#b-cwd").input_value() == "/srv/shop", "cwd prefilled")
    page.locator("#b-about").fill("A bike shop")
    page.locator("#b-gen").click()
    expect(page.locator("#b-name")).to_have_value("Generated Helper")
    check("bike shop" in page.locator("#b-brief").input_value(), "brief generated")
    page.locator("#b-about").fill("fail please"); page.locator("#b-gen").click()
    expect(page.locator("#b-err-persona")).to_be_visible()
    page.locator("#b-cwd").fill("relative/dir"); page.locator("#b-save").click()
    expect(page.locator("#b-err-ctx")).to_have_text(s["cwd"])
    page.locator("#b-cwd").fill("/srv/shop"); page.locator('#b-groups [data-g="-1002"]').click()
    page.locator("#b-save").click()
    expect(page.locator("#b-ok")).to_have_text(s["saved"])
    check(page.locator('#b-groups [data-g="-1002"]').get_attribute("aria-pressed") == "true", "group kept after save")
    if SHOTS: page.screenshot(path=f"{SHOTS}/bots-edit-{lang}-{theme}-390.png")
    over = page.evaluate("[...document.querySelectorAll('#bots-sheet *')].filter(e=>e.offsetParent&&e.getBoundingClientRect().right>innerWidth+0.5).length + (document.documentElement.scrollWidth>innerWidth?1:0)")
    check(over == 0, f"horizontal overflow {over}")
    small = page.evaluate("[...document.querySelectorAll('#bots-sheet button, #bots-sheet input')].filter(e=>e.offsetParent&&e.getBoundingClientRect().height<43.5&&!e.classList.contains('b-back')).map(e=>e.id||e.className)")
    check(not small, f"targets < 44: {small}")
    sheet_box = sheet.bounding_box(); check(sheet_box["height"] <= 844 * .92 + 1, "sheet ceiling")
    page.locator("[data-b-back]:visible").click()
    page.locator("#b-add").click()
    page.locator("#b-token").fill("nocolon"); page.locator("#b-add-go").click()
    expect(page.locator("#b-err-add")).to_have_text(s["token"])
    page.locator("#b-token").fill("123:abc"); page.locator("#b-add-go").click()
    expect(page.locator("#b-vedit")).to_be_visible()
    page.locator("#b-del").click(); expect(page.locator("#b-sure")).to_be_visible()
    page.locator("#b-del-no").click(); expect(page.locator("#b-sure")).to_be_hidden()
    page.locator("#b-del").click(); page.locator("#b-del-yes").click()
    expect(page.locator("#b-list .brow")).to_have_count(2)
    page.keyboard.press("Escape")
    expect(sheet).to_be_hidden()


def main():
    env = dict(os.environ, PORT=str(PORT))
    server = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=env, stdout=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try: urllib.request.urlopen(URL + "/api/bots").read(); break
            except Exception: time.sleep(.1)
        with sync_playwright() as pw:
            browser = getattr(pw, os.environ.get("BROWSER", "chromium")).launch()
            for lang in ("en", "ru"):
                for theme in ("light", "dark"):
                    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ru-RU" if lang == "ru" else "en-US", color_scheme=theme)
                    page = ctx.new_page(); page.set_default_timeout(5000)
                    flow(page, lang, theme)
                    ctx.close()
            browser.close()
    finally:
        server.terminate()
    print("check_bots: OK")


if __name__ == "__main__":
    main()
