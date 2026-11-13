"""Text selection in the session feed survives polling (owner 13.11: «выделение текста через жопу»).
1. The server sends a window of the last 24 entries; the window slides by one — the selected message node stays
   in the DOM with its selection, the feed is not rebuilt under the finger.
2. After the selection is cleared the feed catches up (the new message is visible).
3. Telegram iOS injects `:not(input):not(textarea):not([contenteditable="true"]){-webkit-user-select:none}`:
   .msg stays user-select:text anyway.
Run: python3 tests/webapp/check_select.py   (own stand on PORT, default 18984; Chromium + WebKit, 393×659 touch)"""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).parent; PORT = int(os.environ.get("PORT", "18984")); U = f"http://127.0.0.1:{PORT}"
data = Path(tempfile.gettempdir()) / f"select-{PORT}.json"
SID = "c3e5a7b9"; WIN = 24
base = json.loads((ROOT.parent / "stand" / "demo2.json").read_text())
allm = []
def ts(i): return "2026-11-12T20:%02d:00Z" % i  # минута между записями: голова окна всегда получает разделитель времени t:
def msg(i): return {"role": "assistant", "uuid": f"h{i}", "ts": ts(i), "text": f"строка {i}: " + "длинный текст ленты, " * 6}
def publish():  # как сервер: окно последних 24 записей
    d = json.loads(json.dumps(base)); d["session"][SID]["history"] = allm[-WIN:]; data.write_text(json.dumps(d))
def reset():
    allm[:] = [msg(i) for i in range(30)]; publish()
def grow(txt):
    allm.append({"role": "assistant", "uuid": f"n{len(allm)}", "ts": ts(len(allm)), "text": txt}); publish()
reset()
srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, DATA_FILE=str(data), PORT=str(PORT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U + "/api/sessions", timeout=.3).close(); break
    except OSError: time.sleep(.1)
fails = 0
def check(ok, name, detail=""):
    global fails; fails += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)
SELECT = """()=>{const el=document.querySelector('#s-hist [data-k="h20"] .msg');window.__n=el;el.scrollIntoView({block:'center'});
 const r=document.createRange();r.setStart(el.firstChild,3);r.setEnd(el.firstChild,30);const s=getSelection();s.removeAllRanges();s.addRange(r);return s.toString().length}"""
STATE = "()=>({len:getSelection().toString().length,conn:!!window.__n&&window.__n.isConnected,inside:!!window.__n&&window.__n.contains(getSelection().anchorNode)})"
try:
  with sync_playwright() as pw:
    for eng in ("chromium", "webkit"):
        reset()
        b = getattr(pw, eng).launch(); ctx = b.new_context(viewport={"width": 393, "height": 659}, is_mobile=True, has_touch=True)
        p = ctx.new_page(); errs = []; p.on("pageerror", lambda e: errs.append(str(e)))
        p.goto(U + "/app/"); p.wait_for_timeout(1500)
        p.locator(f'#list .card[data-id="{SID}"]').click(); p.wait_for_timeout(1800)
        n = p.evaluate(SELECT); check(n > 10, f"{eng} selection made", n)
        grow("новое 1: ответ агента, " * 5); p.wait_for_timeout(4500)  # окно сдвинулось: h6 ушёл, n30 пришёл
        st = p.evaluate(STATE)
        check(st == {"len": n, "conn": True, "inside": True}, f"{eng} window slide keeps the selection and its node", st)
        p.evaluate("getSelection().removeAllRanges()"); p.wait_for_timeout(3500)
        check(p.evaluate("document.querySelector('#s-hist').textContent.includes('новое 1')"), f"{eng} feed catches up after the selection is cleared")
        check(p.evaluate("!document.querySelector('#s-hist [data-k=\"h6\"]')"), f"{eng} scrolled-out entry is gone")
        # 4. без выделения сдвиг окна не пересобирает ленту: узел той же записи остаётся тем же объектом
        p.evaluate("()=>{window.__m=document.querySelector('#s-hist [data-k=h20]')}")
        grow("новое 2: ответ агента, " * 5); p.wait_for_timeout(4500)
        kept = p.evaluate("()=>({same:!!window.__m&&window.__m.isConnected,head:[...document.querySelectorAll('#s-hist [data-k]')].slice(0,2).map(e=>e.dataset.k),n2:document.querySelector('#s-hist').textContent.includes('новое 2')})")
        check(kept["same"] and kept["n2"] and kept["head"][0].startswith("t:"), f"{eng} window slide without selection keeps nodes (head separator inserted)", kept)
        p.evaluate("""()=>{const s=document.createElement('style');s.textContent=':not(input):not(textarea):not([contenteditable="true"]){-webkit-user-select:none}*{-webkit-touch-callout:none}';document.head.append(s)}""")
        us = p.evaluate("()=>{const c=getComputedStyle(document.querySelector('#s-hist .msg'));return c.userSelect||c.webkitUserSelect}")
        check(us == "text", f"{eng} .msg stays selectable under Telegram's user-select:none", us)
        check(not errs, f"{eng} no page errors", errs)
        b.close()
finally:
    srv.terminate()
print("TOTAL", "PASS" if not fails else "FAIL"); sys.exit(1 if fails else 0)
