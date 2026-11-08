"""Лента сессии держит низ, только пока пользователь сам внизу (дефект владельца 08.11 «тот же автоскролл»).
Живая сессия пишет новые строки; пользователь отлистал вверх на 60 и на 500 px — лента стоит, и в первые 3 мин
после своей отправки тоже. Вернулся вниз — снова держит низ. Своя отправка возвращает вниз.
Run: python3 tests/webapp/check_follow.py   (свой стенд на PORT, по умолчанию 18983; Chromium + WebKit, телефон 390×844)"""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).parent; PORT = int(os.environ.get("PORT", "18983")); U = f"http://127.0.0.1:{PORT}"
data = Path(tempfile.gettempdir()) / f"follow-{PORT}.json"
SID = "c3e5a7b9"  # сессия в работе
base = json.loads((ROOT.parent / "stand" / "demo2.json").read_text())
seq = [0]
def reset():
    d = json.loads(json.dumps(base))
    d["session"][SID]["history"] = [{"role": "assistant" if i % 2 else "user", "uuid": f"h{i}", "text": f"строка {i}: " + "длинный текст ленты, " * 8} for i in range(40)]
    data.write_text(json.dumps(d))
def grow():  # агент дописал строку (как живая сессия)
    seq[0] += 1; d = json.loads(data.read_text())
    d["session"][SID]["history"].append({"role": "assistant", "uuid": f"n{seq[0]}", "text": f"новое {seq[0]}: " + "ответ агента, " * 10})
    data.write_text(json.dumps(d))
reset()
srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, DATA_FILE=str(data), PORT=str(PORT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U + "/api/sessions", timeout=.3).close(); break
    except OSError: time.sleep(.1)
fails = 0
def check(ok, name, detail=""):
    global fails; fails += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)
POS = "()=>{const b=document.getElementById('s-body');return {top:Math.round(b.scrollTop),gap:Math.round(b.scrollHeight-b.scrollTop-b.clientHeight)}}"
try:
  with sync_playwright() as pw:
    for eng in ("chromium", "webkit"):
        reset()
        b = getattr(pw, eng).launch(); ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
        p = ctx.new_page(); errs = []; p.on("pageerror", lambda e: errs.append(str(e)))
        p.goto(U + "/app/"); p.wait_for_timeout(1500)
        p.locator(f'#list .card[data-id="{SID}"]').click(); p.wait_for_timeout(1800)
        check(p.evaluate(POS)["gap"] < 24, f"{eng} opens at the end", p.evaluate(POS))
        def read_up(dy, label):
            p.mouse.move(195, 420); p.mouse.wheel(0, -dy); p.wait_for_timeout(400)
            before = p.evaluate(POS)["top"]
            grow(); p.wait_for_timeout(4500)  # опрос ленты в работе ~1,5 с: два-три цикла
            after = p.evaluate(POS)["top"]
            check(abs(after - before) < 4, f"{eng} {label}: a new line does not pull the feed down", f"{before}→{after}")
        read_up(60, "read up 60 px")
        p.mouse.wheel(0, 99999); p.wait_for_timeout(600)
        grow(); p.wait_for_timeout(4500)
        check(p.evaluate(POS)["gap"] < 24, f"{eng} at the end: follows new lines", p.evaluate(POS))
        # своя отправка: вниз; потом пользователь листает вверх — 3 мин «держать низ» его не перебивают
        p.locator("#s-text").fill("проверка"); p.locator("#s-send").click(); p.wait_for_timeout(1800)
        p.locator("#s-text").blur(); p.wait_for_timeout(600)
        check(p.evaluate(POS)["gap"] < 24, f"{eng} own message: feed at the end", p.evaluate(POS))
        read_up(500, "after sending, read up 500 px")
        check(not errs, f"{eng} no page errors", errs)
        b.close()
finally:
    srv.terminate()
print("TOTAL", "PASS" if not fails else "FAIL"); sys.exit(1 if fails else 0)
