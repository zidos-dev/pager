"""Тап по плиткам квоты открывает лист «Квоты»: строки Claude (5 ч, 7 дн) + строки CC_STATUS_FILE, «Обновить» шлёт POST
и показывает, когда можно обновить снова (сервер не зовёт команду чаще мин. интервала). Клавиатура: Enter на плитках.
Run: python3 tests/webapp/check_quota_sheet.py   (свой стенд на PORT, по умолчанию 18983; Chromium + WebKit, 393×659)"""
import os, sys, subprocess, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).parent; PORT = int(os.environ.get("PORT", "18983")); U = f"http://127.0.0.1:{PORT}"
srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, PORT=str(PORT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U + "/api/sessions", timeout=.3).close(); break
    except OSError: time.sleep(.1)
fails = 0
def check(ok, name, detail=""):
    global fails; fails += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)
try:
  with sync_playwright() as pw:
    for eng in ("chromium", "webkit"):
        b = getattr(pw, eng).launch(); ctx = b.new_context(viewport={"width": 393, "height": 659}, has_touch=True, service_workers="block", locale="ru-RU")
        p = ctx.new_page(); errs = []; p.on("pageerror", lambda e: errs.append(str(e)))
        posts = []; p.on("request", lambda r: posts.append(r.url) if r.method == "POST" and r.url.endswith("/api/quota/refresh") else None)
        p.goto(U + "/app/"); p.wait_for_timeout(1500)
        p.click("#lab-q"); p.wait_for_timeout(900)
        sh = p.locator("#quota-sheet")
        check(sh.is_visible(), f"{eng}: tap on the tiles opens the sheet")
        rows = p.locator("#q-rows .qr")
        check(rows.count() == 6, f"{eng}: Claude 5 h + 7 d + 4 status rows", str(rows.count()))
        check("34 %" in rows.nth(0).inner_text(), f"{eng}: first row is Claude 5 h", rows.nth(0).inner_text())
        check(p.locator("#q-rows .qp.hot").count() == 1, f"{eng}: only the hot row is marked")
        btn = p.locator("#q-refresh").bounding_box()
        check(btn and btn["y"] + btn["height"] <= 659 and btn["height"] >= 44, f"{eng}: refresh button fits and is a 44 px target", str(btn))
        p.click("#q-refresh"); p.wait_for_timeout(700)
        check(len(posts) == 1, f"{eng}: refresh is one POST", str(len(posts)))
        check("обновить можно" in p.locator("#q-at").inner_text(), f"{eng}: shows when the next refresh is possible", p.locator("#q-at").inner_text())
        p.keyboard.press("Escape"); p.wait_for_timeout(700)
        check(not sh.is_visible(), f"{eng}: Escape closes the sheet")
        p.focus("#lab-q"); p.keyboard.press("Enter"); p.wait_for_timeout(900)
        check(sh.is_visible(), f"{eng}: Enter on the focused tiles opens the sheet")
        check(not errs, f"{eng}: no page errors", str(errs))
        b.close()
finally:
    srv.kill()
print("FAILS", fails); sys.exit(1 if fails else 0)
