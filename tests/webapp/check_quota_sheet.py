"""Тап по плиткам квоты открывает лист «Квоты»: строки Claude (5 ч, 7 дн) + строки CC_STATUS_FILE, «Обновить» шлёт POST
и показывает, когда можно обновить снова (сервер не зовёт команду чаще мин. интервала). Клавиатура: Enter на плитках.
Run: python3 tests/webapp/check_quota_sheet.py   (свой стенд на PORT, по умолчанию 18983; Chromium + WebKit, 393×659)"""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).parent; PORT = int(os.environ.get("PORT", "18983")); U = f"http://127.0.0.1:{PORT}"
# Плитки на списке есть, только когда никто не ждёт: демо без ожидающих, чтобы сетка и закрепление проверялись как раньше.
_demo = json.loads((ROOT.parent / "stand" / "demo2.json").read_text())
for _row in _demo["sessions"]["sessions"]:
    if _row.get("group") == "wait" or _row.get("perm"):
        _row["group"] = "work"
        _row["perm"] = False
_data = Path(tempfile.gettempdir()) / f"quota-sheet-{PORT}.json"
_data.write_text(json.dumps(_demo))
srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, PORT=str(PORT), DATA_FILE=str(_data)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        # «+» у строки выносит её плиткой в шапку; у Claude и строки без процента кнопки нет
        pins = p.locator("#q-rows .qpin")
        check(pins.count() == 3, f"{eng}: pin buttons only on provider rows with a percent", str(pins.count()))
        pb = pins.first.bounding_box()
        check(pb and pb["width"] >= 44 and pb["height"] >= 44, f"{eng}: pin button is a 44 px target", str(pb))
        tiles = p.locator("#lab-q .lqt:not([hidden])")
        check(tiles.count() == 2, f"{eng}: two Claude tiles before pinning", str(tiles.count()))
        p.locator('#q-rows [data-pin="codex"]').click(); p.wait_for_timeout(1200)
        check(p.locator('#q-rows [data-pin="codex"]').get_attribute("aria-pressed") == "true", f"{eng}: pinned row shows as pressed")
        check(tiles.count() == 3 and "Codex" in tiles.nth(2).inner_text(), f"{eng}: Codex tile appears after Claude", str([tiles.nth(i).inner_text() for i in range(tiles.count())]))
        check("41" in tiles.nth(2).get_attribute("aria-label"), f"{eng}: tile carries the row's percent", tiles.nth(2).get_attribute("aria-label"))
        p.keyboard.press("Escape")
        try: sh.wait_for(state="hidden", timeout=4000)  # the close animation under a loaded host takes longer than a fixed 700 ms
        except Exception: pass
        check(not sh.is_visible(), f"{eng}: Escape closes the sheet")
        p.reload(); p.wait_for_timeout(1500)
        check(p.locator("#lab-q .lqt:not([hidden])").count() == 3, f"{eng}: the pin survives a reload (server keeps it)")
        lw = p.evaluate("Math.max(...[...document.querySelectorAll('#lab-q .lql')].map(e=>e.scrollWidth-e.clientWidth))")
        check(lw <= 1 or p.evaluate("[...document.querySelectorAll('#lab-q .lql')].every(e=>getComputedStyle(e).textOverflow==='ellipsis')"), f"{eng}: tile labels never overflow", str(lw))
        # >2 tiles: a dense grid, three in one row (two-column tiles took a third of a phone screen, council 09.11)
        tops = {round(b["y"]) for b in (t.bounding_box() for t in p.locator("#lab-q .lqt:not([hidden])").all())}
        check(len(tops) == 1, f"{eng}: three tiles share one row", str(tops))
        # 1440: columns of fractional width; a poll with the same values must not replay the bar width (idle long frames, kit 09.11)
        p.set_viewport_size({"width": 1440, "height": 900}); p.wait_for_timeout(600)
        p.evaluate("window.__bars=0;(function f(){window.__bars+=[...document.querySelectorAll('#lab-q .lqc i')].reduce((n,i)=>n+i.getAnimations().length,0);requestAnimationFrame(f)})()")
        p.wait_for_timeout(10000)  # one list poll
        bars = p.evaluate("window.__bars")
        check(bars == 0, f"{eng}: an unchanged poll does not animate the bars", str(bars))
        p.set_viewport_size({"width": 393, "height": 659}); p.wait_for_timeout(600)
        p.click("#lab-q"); p.wait_for_timeout(900)
        p.locator('#q-rows [data-pin="codex"]').click(); p.wait_for_timeout(1200)
        check(p.locator("#lab-q .lqt:not([hidden])").count() == 2, f"{eng}: unpinning removes the tile")
        p.keyboard.press("Escape"); p.wait_for_timeout(700)
        p.focus("#lab-q"); p.keyboard.press("Enter"); p.wait_for_timeout(900)
        check(sh.is_visible(), f"{eng}: Enter on the focused tiles opens the sheet")
        check(not errs, f"{eng}: no page errors", str(errs))
        b.close()
finally:
    srv.kill()
    _data.unlink(missing_ok=True)
# Ждущие сессии: плитки квот остаются плитками (владелец 13.11 01:52).
QP = PORT + 2; QU = f"http://127.0.0.1:{QP}"
_qdata = Path(tempfile.gettempdir()) / f"quota-quiet-{QP}.json"
_qdata.write_text(json.dumps(json.loads((ROOT.parent / "stand" / "demo2.json").read_text())))
qsrv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, PORT=str(QP), DATA_FILE=str(_qdata)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(QU + "/api/sessions", timeout=.3).close(); break
    except OSError: time.sleep(.1)
try:
  urllib.request.urlopen(urllib.request.Request(QU + "/api/quota/pins", data=json.dumps({"id": "glm", "on": True}).encode(), method="POST")).close()
  with sync_playwright() as pw:
    for eng in ("chromium", "webkit"):
        b = getattr(pw, eng).launch(); ctx = b.new_context(viewport={"width": 393, "height": 659}, has_touch=True, service_workers="block", locale="ru-RU")
        p = ctx.new_page(); p.goto(QU + "/app/"); p.wait_for_timeout(1500)
        # Владелец 13.11 01:52: плашки нужны и при ждущих сессиях, тихой строки больше нет.
        check(not p.locator("#lab-q .lqline").is_visible(), f"{eng}: waiters -> no quiet line")
        tiles = p.locator("#lab-q .lqt:not([hidden])")
        check(tiles.count() >= 3, f"{eng}: waiters -> tiles are shown", tiles.count())
        labs = tiles.evaluate_all("ts => ts.map(t => t.getAttribute('aria-label'))")
        check(any("GLM" in l and "88" in l for l in labs), f"{eng}: pinned GLM tile is shown with its value", labs)
        check(p.locator("#lab-q .lqt.hot:not([hidden])").count() >= 1, f"{eng}: pinned GLM tile is hot")
        b.close()
finally:
    qsrv.kill(); _qdata.unlink(missing_ok=True)
print("FAILS", fails); sys.exit(1 if fails else 0)
