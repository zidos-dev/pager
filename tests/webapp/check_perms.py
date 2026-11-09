"""Sheet «Permissions» and the bot's permissions block against the stand's synthetic /api/perms (README «Permissions»):
mode segment, rules added/removed/saved, a rule Claude Code would not read, the group bot without «Everything»,
the «Everywhere» option on an SDK request, quiet lines of rule decisions in the feed.
RU/EN x light/dark at 390x844, no horizontal overflow, targets >= 44, sheet <= 92dvh. SHOTS=<dir> saves screenshots.
Run: python3 tests/webapp/check_perms.py   (starts its own stand on PORT, default 18991; PW_BROWSER=webkit for WebKit)
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
PORT = int(os.environ.get("PORT", "18991"))
URL = f"http://127.0.0.1:{PORT}"
SHOTS = os.environ.get("SHOTS")
T = {"en": dict(hold="Hold — the rule stays for good", hold2="Press twice — the rule stays for good", again="Press again — the rule stays for good", all="Everything", edits="Edits", saved="Saved.", bad="Claude Code would not read this rule: bad rule", allowed="Allowed by rule Bash(git status:*): git status", everywhere="Everywhere: Bash(npm test:*)"),
     "ru": dict(hold="Удерживайте — правило навсегда", hold2="Нажмите дважды — правило навсегда", again="Нажмите ещё раз — правило навсегда", all="Всё", edits="Правки", saved="Сохранено.", bad="Claude Code не прочитает это правило: bad rule", allowed="Разрешено правилом Bash(git status:*): git status", everywhere="Везде: Bash(npm test:*)")}


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def post(path, body):
    urllib.request.urlopen(urllib.request.Request(URL + path, data=json.dumps(body).encode(), method="POST")).read()


def layout(page, sel):
    over = page.evaluate(f"[...document.querySelectorAll('{sel} *')].filter(e=>e.offsetParent&&e.getBoundingClientRect().right>innerWidth+0.5).length + (document.documentElement.scrollWidth>innerWidth?1:0)")
    check(over == 0, f"horizontal overflow {over}")
    small = page.evaluate(f"[...document.querySelectorAll('{sel} button, {sel} input')].filter(e=>e.offsetParent&&e.getBoundingClientRect().height<43.5&&!e.classList.contains('b-back')).map(e=>e.id||e.className||e.textContent)")
    check(not small, f"targets < 44: {small}")
    check(page.locator(sel).bounding_box()["height"] <= 844 * .92 + 1, "sheet ceiling")


def perm_bodies():
    return [json.loads(x)["body"] for x in open(PERM_LOG)] if os.path.exists(PERM_LOG) else []


def perm_posts():
    return len(perm_bodies())


def reduced(browser):
    """Reduced motion: «Everywhere» takes two presses instead of a hold."""
    stand_data(DATA)
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="ru-RU", reduced_motion="reduce")
    page = ctx.new_page(); page.set_default_timeout(5000)
    page.goto(f"{URL}/app/", wait_until="load")
    page.locator(f'.card[data-id="{SID}"]').click()
    row = page.locator(".lab-ask-root .lab-ask-opt.perm-every")
    expect(row).to_contain_text(T["ru"]["hold2"])
    n0 = perm_posts()
    row.click(); page.wait_for_timeout(400)
    check(perm_posts() == n0 and T["ru"]["again"] in row.inner_text(), "first press arms")
    row.click(); page.wait_for_timeout(800)
    check([b["option"] for b in perm_bodies()[n0:]] == [4], "second press sends option 4")
    ctx.close()


def flow(page, lang, theme):
    s = T[lang]
    stand_data(DATA)
    post("/api/perms", {"reset": True}); post("/api/bots/reset", {})
    page.goto(f"{URL}/app/?theme=tg-{theme}", wait_until="load")
    page.locator("#perms-btn").click()
    sheet = page.locator("#perms-sheet")
    expect(sheet).to_be_visible()
    seg = page.locator("#p-block .pm-seg [data-mode]")
    expect(seg).to_have_count(3)
    check(page.locator('#p-block [data-mode="edits"]').get_attribute("aria-checked") == "true", "mode from the server")
    expect(page.locator("#p-block .pm-r")).to_have_count(4)
    # two switches, each under its own label (09.11: «два уровня переключателей» read as one)
    expect(page.locator("#p-whose")).to_be_visible()
    expect(page.locator("#p-block .pm-ml")).to_be_visible()
    # common rules fill the field; one already in a list is not offered
    check(page.locator('#p-block .pm-q [data-q="Bash(git status:*)"]').count() == 0, "a rule already allowed is offered again")
    page.locator('#p-block .pm-q [data-q="Bash(git diff:*)"]').click()
    expect(page.locator("#p-block .pm-in")).to_have_value("Bash(git diff:*)")
    page.locator("#p-block .pm-in").fill("")
    page.wait_for_timeout(450)
    if SHOTS: page.screenshot(path=f"{SHOTS}/perms-{lang}-{theme}-390.png")
    layout(page, "#perms-sheet")
    # the segment's white plate moves to the chosen mode
    page.locator('#p-block [data-mode="all"]').click()
    page.wait_for_timeout(400)
    th, b = page.locator("#p-block .pm-th").bounding_box(), page.locator('#p-block [data-mode="all"]').bounding_box()
    check(abs(th["x"] - b["x"]) < 2 and abs(th["width"] - b["width"]) < 2, f"plate under the chosen mode {th} {b}")
    # add and remove rules; a broken rule is shown with its text
    page.locator("#p-block .pm-in").fill("Bash(ls:*)"); page.locator('#p-block [data-add="allow"]').click()
    expect(page.locator("#p-block .pm-r")).to_have_count(5)
    page.locator('#p-block [data-rm="deny"]').click()
    expect(page.locator("#p-block .pm-r")).to_have_count(4)
    page.locator("#p-block .pm-in").fill("bad rule"); page.locator('#p-block [data-add="deny"]').click()
    # the form is checked before adding: not added, the text stays in the field to fix
    expect(page.locator("#p-block .m-err")).to_have_text(s["bad"])
    expect(page.locator("#p-block .pm-r")).to_have_count(4)
    expect(page.locator("#p-block .pm-in")).to_have_value("bad rule")
    page.locator("#p-save").click()
    expect(page.locator("#p-block .m-err")).to_have_text(s["bad"])
    expect(page.locator("#p-block .pm-r.bad")).to_have_count(1)
    page.locator("#p-block .pm-r.bad button").click()
    page.locator("#p-save").click()
    expect(page.locator("#p-ok")).to_have_text(s["saved"])
    saved = json.load(urllib.request.urlopen(URL + "/api/perms"))["sessions"]
    check(saved == {"mode": "all", "allow": ["Bash(git status:*)", "Bash(npm test:*)", "WebFetch(domain:docs.python.org)", "Bash(ls:*)"], "deny": []}, f"saved {saved}")
    # the group bot: two modes only
    page.locator('#p-target [data-t="chat"]').click()
    expect(page.locator("#p-block .pm-seg [data-mode]")).to_have_count(2)
    check(page.locator("#p-block .pm-r").count() == 1, "chat rules")
    layout(page, "#perms-sheet")
    page.keyboard.press("Escape")
    expect(sheet).to_be_hidden()
    # a bot: permissions block inside the edit form, saved with the bot
    page.locator("#bots-btn").click()
    page.locator("#b-list .brow").nth(0).click()
    expect(page.locator("#b-perms .pm-seg [data-mode]")).to_have_count(2)
    expect(page.locator("#b-perms .pm-r")).to_have_count(3)
    page.locator('#b-perms [data-mode="edits"]').click()
    page.locator("#b-perms .pm-in").fill("Bash(npm run lint)")  # typed, not added: kept on save
    page.locator("#b-save").click()
    expect(page.locator("#b-ok")).to_have_text(s["saved"])
    bp = json.load(urllib.request.urlopen(URL + "/api/bots"))["bots"][0]["perms"]
    check(bp == {"mode": "edits", "allow": ["Read", "Grep", "Bash(npm run lint)"], "deny": ["Bash(git push:*)"]}, f"bot perms {bp}")
    page.locator("#b-perms").scroll_into_view_if_needed()
    if SHOTS: page.screenshot(path=f"{SHOTS}/perms-bot-{lang}-{theme}-390.png")
    layout(page, "#bots-sheet")
    page.keyboard.press("Escape")
    # feed: rule decisions as quiet lines; an SDK request offers «Everywhere» (option 4)
    sid = SID
    page.locator(f'.card[data-id="{sid}"]').click()
    expect(page.locator(".note.pm-auto")).to_have_text(s["allowed"])
    opt = page.locator('#s-question [data-a="perm"][data-i="4"]')
    expect(opt).to_have_text("3" + s["everywhere"])  # SDK: number = place, deny last
    labels = page.locator('#s-question [data-a="perm"]').all_inner_texts()
    check(labels[-1].endswith("Deny"), f"deny stays last {labels}")
    expect(page.locator(".lab-ask-root .lab-ask-mk")).to_have_count(4)
    marks = page.locator(".lab-ask-root .lab-ask-mk").all_inner_texts()  # the sheet the owner sees (layers.js), not the hidden card
    check(marks == ["1", "2", "3", "4"], f"SDK options numbered by place in the sheet {marks}")
    # «♾ Везде» — удержание (выбор владельца 05.11): касание ничего не шлёт, удержание 0,8 с — шлёт вариант 4
    row = page.locator(".lab-ask-root .lab-ask-opt.perm-every")
    expect(row).to_have_count(1)
    check(s["hold"] in row.inner_text(), f"hold hint {row.inner_text()!r}")
    n0 = perm_posts()
    row.click(); page.wait_for_timeout(700)
    check(perm_posts() == n0, "a tap on «Everywhere» sends nothing")
    bb = row.bounding_box(); page.mouse.move(bb["x"] + 40, bb["y"] + bb["height"] / 2); page.mouse.down(); page.wait_for_timeout(1100); page.mouse.up()
    page.wait_for_timeout(800)
    posts = perm_bodies()[n0:]
    check(len(posts) == 1 and posts[0]["option"] == 4, f"holding sends option 4 once {posts}")
    if SHOTS: page.screenshot(path=f"{SHOTS}/perms-feed-{lang}-{theme}-390.png")


SID = "b2d4f6a8"  # a waiting session without a question of its own


def stand_data(path):
    """demo2.json + rule decisions and an SDK request with «Everywhere» on SID (page.route misses WebKit's fetches)."""
    d = json.load(open(ROOT.parent / "stand" / "demo2.json"))
    s = d["session"].setdefault(SID, {"id": SID, "name": SID, "group": "wait", "history": [], "cwd": "~", "question": None})
    s["auto"] = [{"tool": "Bash", "what": "git status", "verdict": "allow", "rule": "Bash(git status:*)", "ts": time.time() - 30}]
    s["perm"] = {"kind": "sdk", "req": "r1", "title": "Bash", "summary": "npm test", "everywhere": "Bash(npm test:*)",
                 "options": [{"n": 1, "label": "Allow"}, {"n": 2, "label": "Always in this session"}, {"n": 3, "label": "Deny"}]}
    json.dump(d, open(path, "w"), ensure_ascii=False)


def main():
    import tempfile
    global DATA, PERM_LOG
    tmp = tempfile.mkdtemp(); DATA = os.path.join(tmp, "demo.json"); PERM_LOG = os.path.join(tmp, "perm.jsonl"); stand_data(DATA)
    env = dict(os.environ, PORT=str(PORT), DATA_FILE=DATA, PERM_LOG=PERM_LOG)
    server = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=env, stdout=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try: urllib.request.urlopen(URL + "/api/perms").read(); break
            except Exception: time.sleep(.1)
        with sync_playwright() as pw:
            browser = getattr(pw, os.environ.get("PW_BROWSER", "chromium")).launch()
            for lang in ("en", "ru"):
                for theme in ("light", "dark"):
                    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=os.environ.get("PW_BROWSER", "chromium") != "firefox", has_touch=True, locale="ru-RU" if lang == "ru" else "en-US", color_scheme=theme)
                    page = ctx.new_page(); page.set_default_timeout(5000)
                    flow(page, lang, theme)
                    ctx.close()
            reduced(browser)
            browser.close()
    finally:
        server.terminate()
    print("check_perms: OK")


if __name__ == "__main__":
    main()
