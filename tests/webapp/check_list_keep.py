"""Выход из сессии не прокручивает список (дефект владельца 08.11 20:30 «после выхода из окна чата идёт автоскролл
ко дну страницы списка чатов»). Список прокручен в середину; открыть сессию, (а) просто выйти, (б) тапнуть поле и выйти,
(в) выйти, пока лента дописывается — список стоит там же, где был.
Run: python3 tests/webapp/check_list_keep.py   (свой стенд на PORT, по умолчанию 18984; Chromium + WebKit, 393×659)"""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).parent; PORT = int(os.environ.get("PORT", "18984")); U = f"http://127.0.0.1:{PORT}"
data = Path(tempfile.gettempdir()) / f"listkeep-{PORT}.json"
d = json.loads((ROOT.parent / "stand" / "demo2.json").read_text())
proto = next(s for s in d["sessions"]["sessions"] if s["group"] == "work")
for i in range(30):  # длинный список: есть куда листать
    sid = f"e{i:07x}"
    d["sessions"]["sessions"].append(dict(proto, id=sid, name=f"Задача {i}", base_name=f"Задача {i}", group="done", state="done"))
    d["session"][sid] = dict(d["session"][proto["id"]], id=sid, name=f"Задача {i}",
                             history=[{"role": "assistant" if j % 2 else "user", "uuid": f"{sid}-{j}", "text": f"строка {j}: " + "текст ленты, " * 8} for j in range(30)])
data.write_text(json.dumps(d))
srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, DATA_FILE=str(data), PORT=str(PORT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U + "/api/sessions", timeout=.3).close(); break
    except OSError: time.sleep(.1)
fails = 0
def check(ok, name, detail=""):
    global fails; fails += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)
MID = "()=>{const cs=[...document.querySelectorAll('#list .card[data-id]')];const c=cs.find(c=>{const r=c.getBoundingClientRect();return r.top>200&&r.bottom<innerHeight-120});return c&&c.dataset.id}"
try:
  with sync_playwright() as pw:
    for eng in ("chromium", "webkit"):
        b = getattr(pw, eng).launch(); ctx = b.new_context(viewport={"width": 393, "height": 659}, has_touch=True, service_workers="block")
        p = ctx.new_page(); errs = []; p.on("pageerror", lambda e: errs.append(str(e)))
        p.goto(U + "/app/"); p.wait_for_timeout(1500)
        for how in ("plain", "focus", "writing"):
            p.evaluate("window.scrollTo(0, Math.round((document.body.scrollHeight-innerHeight)/2))"); p.wait_for_timeout(500)
            y0 = p.evaluate("scrollY"); sid = p.evaluate(MID)
            p.locator(f'#list .card[data-id="{sid}"]').click(); p.wait_for_timeout(1500)
            if how == "focus":
                p.locator("#s-text").click(); p.wait_for_timeout(120)  # фокус ставит таймеры прокрутки ленты; выход до их срабатывания
                p.locator("#s-text").blur()
            if how == "writing":  # лента внизу дописывается: отрисовка держит низ отложенно
                p.evaluate("document.getElementById('s-body').scrollTop=1e6"); p.wait_for_timeout(300)
            p.keyboard.press("Escape"); p.wait_for_timeout(1800)
            y1 = p.evaluate("scrollY"); maxy = p.evaluate("document.body.scrollHeight-innerHeight")
            check(abs(y1 - y0) < 4, f"{eng} {how}: the list stays where it was", f"{y0}→{y1} (max {maxy}, {sid})")
            check(p.evaluate("document.documentElement.dataset.view") == "list", f"{eng} {how}: back on the list")
        check(not errs, f"{eng} no page errors", errs)
        b.close()
finally:
    srv.terminate()
print("TOTAL", "PASS" if not fails else "FAIL"); sys.exit(1 if fails else 0)
