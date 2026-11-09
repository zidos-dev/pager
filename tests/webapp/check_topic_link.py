"""Меню сессии «Открыть в чате» (Bot API 10.4: t.me/<бот>/<тема> открывает тему лички с ботом): пункт есть, когда сервер
отдал имя бота и тему сессии, и ведёт на ссылку темы; без имени бота (старый сервер, нет тем) пункта нет.
Run: python3 tests/webapp/check_topic_link.py   (свой стенд на PORT, по умолчанию 18984; Chromium + WebKit, 390×844)"""
import json, os, re, sys, subprocess, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).parent; PORT = int(os.environ.get("PORT", "18984")); U = f"http://127.0.0.1:{PORT}"
srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, PORT=str(PORT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U + "/api/sessions", timeout=.3).close(); break
    except OSError: time.sleep(.1)
fails = 0
def check(ok, name, detail=""):
    global fails; fails += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)

def no_bot(route):
    r = route.fetch(); j = r.json(); j.pop("bot", None); route.fulfill(response=r, json=j)

def open_menu(p):
    p.goto(U + "/app/"); p.wait_for_timeout(1300)
    p.locator('#list .card[data-id="c3e5a7b9"]').click(); p.wait_for_timeout(1200)  # без открытого вопроса
    p.locator("#s-more").click(); m = p.locator('.mn-root [role="menu"]'); m.wait_for(state="visible")
    m.evaluate("e=>Promise.all(e.getAnimations({subtree:true}).map(a=>a.finished.catch(()=>{})))")
    return m
try:
  with sync_playwright() as pw:
    for eng in ("chromium", "webkit"):
        b = getattr(pw, eng).launch()
        for plain in (False, True):
            ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, service_workers="block", locale="ru-RU")
            ctx.add_init_script("window.open = u => { window.__opened = u; return null; }")
            p = ctx.new_page(); errs = []; p.on("pageerror", lambda e: errs.append(str(e)))
            if plain: p.route("**/api/sessions", no_bot)
            m = open_menu(p); row = m.locator('[data-k="topic"]')
            if plain:
                check(row.count() == 0, f"{eng}: no bot name — no «Открыть в чате»")
            else:
                check(row.count() == 1 and "Открыть в чате" in row.inner_text(), f"{eng}: the row is in the menu", row.inner_text() if row.count() else "")
                bx = row.bounding_box()
                check(bx and bx["height"] >= 44, f"{eng}: 44 px target", str(bx))
                row.click(); p.wait_for_timeout(500)
                u = p.evaluate("window.__opened")
                check(bool(re.fullmatch(r"https://t\.me/pager_demo_bot/8\d\d", u or "")), f"{eng}: opens the topic link", str(u))
                check(m.is_hidden(), f"{eng}: the menu closes")
            check(not errs, f"{eng}: no page errors", json.dumps(errs))
            ctx.close()
        b.close()
finally:
    srv.terminate()
print("RESULT", "PASS" if not fails else f"FAIL {fails}"); sys.exit(1 if fails else 0)
