"""Long press on a feed bubble opens a one-item «Копировать» menu (UX audit P3 №17).
Hold 600 ms on an agent reply or own message → menu (role=menu/menuitem, ≥44 px, inside the window); tap on it copies the full text + toast.
No menu: short tap, move ≥8 px, ghost .pending, existing selection, selection made during the hold. Menu closes on outside tap, scroll, Esc.
Run: python3 tests/webapp/check_long_press.py   (own stand on PORT, default 19451; Chromium + WebKit, 393×659 touch)"""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).parent; PORT = int(os.environ.get("PORT", "19451")); U = f"http://127.0.0.1:{PORT}"
data = Path(tempfile.gettempdir()) / f"lp-{PORT}.json"
SID = "c3e5a7b9"
base = json.loads((ROOT.parent / "stand" / "demo2.json").read_text())
hist = [{"role": "user", "uuid": "u1", "text": "мой вопрос\nвторая строка"},
        {"role": "assistant", "uuid": "a1", "html": "<p>Первый ответ</p><p>Второй абзац</p>"}]
d = json.loads(json.dumps(base)); d["session"][SID]["history"] = hist; data.write_text(json.dumps(d))
srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, DATA_FILE=str(data), PORT=str(PORT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U + "/api/sessions", timeout=.3).close(); break
    except OSError: time.sleep(.1)
fails = 0
def check(ok, name, detail=""):
    global fails; fails += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)
HOOK = """()=>{window.__cp=[];const w=navigator.clipboard&&navigator.clipboard.writeText?navigator.clipboard.writeText.bind(navigator.clipboard):null;
 if(w)navigator.clipboard.writeText=t=>{window.__cp.push('async:'+t);return w(t)};document.addEventListener('copy',()=>{const a=document.activeElement;window.__cp.push('legacy:'+(a&&a.value!=null?a.value.slice(a.selectionStart,a.selectionEnd):getSelection().toString()))},true)}"""
# synthetic touch pointer events on the element; moves go to document like real ones
EV = """([sel,type,dx,dy])=>{const el=document.querySelector(sel),r=el.getBoundingClientRect(),x=r.left+Math.min(40,r.width/2),y=r.top+r.height/2;
 const o={bubbles:true,cancelable:true,pointerId:7,pointerType:'touch',isPrimary:true,clientX:x+dx,clientY:y+dy};el.dispatchEvent(new PointerEvent(type,o))}"""
def ev(p, sel, type, dx=0, dy=0): p.evaluate(EV, [sel, type, dx, dy])
def hold(p, sel, ms, dx=0):
    ev(p, sel, "pointerdown")
    if dx: ev(p, sel, "pointermove", dx)
    p.wait_for_timeout(ms); ev(p, sel, "pointerup", dx)
menu = lambda p: p.evaluate("!!document.querySelector('.lpm')")
AG, ME = '#s-hist [data-k="a1"] .msg', '#s-hist .me'
try:
  with sync_playwright() as pw:
    for eng in ("chromium", "webkit"):
        b = getattr(pw, eng).launch(); ctx = b.new_context(viewport={"width": 393, "height": 659}, is_mobile=True, has_touch=True, locale="ru-RU")
        if eng == "chromium": ctx.grant_permissions(["clipboard-read", "clipboard-write"])
        p = ctx.new_page(); errs = []; p.on("pageerror", lambda e: errs.append(str(e)))
        p.goto(U + "/app/"); p.wait_for_timeout(1500)
        p.locator(f'#list .card[data-id="{SID}"]').click(); p.wait_for_timeout(1800); p.evaluate(HOOK)
        hold(p, AG, 100); check(not menu(p), f"{eng} short tap: no menu")
        hold(p, AG, 600, dx=20); check(not menu(p), f"{eng} hold with 20 px drift: no menu")
        hold(p, AG, 600)
        g = p.evaluate("()=>{const m=document.querySelector('.lpm');if(!m)return null;const b=m.querySelector('button'),r=m.getBoundingClientRect();return {role:m.getAttribute('role'),item:b.getAttribute('role'),txt:b.textContent,h:b.getBoundingClientRect().height,n:m.children.length,inside:r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight}}")
        check(g and g["role"] == "menu" and g["item"] == "menuitem" and g["txt"] == "Копировать" and g["n"] == 1 and g["h"] >= 43.9 and g["inside"], f"{eng} hold on reply: one-item menu in window", g)
        p.locator(".lpm button").click(); p.wait_for_timeout(300)
        cp = p.evaluate("window.__cp"); tx = p.evaluate("document.querySelector('#toast').textContent")
        check(cp and cp[-1].split(":", 1)[1] == "Первый ответ\n\nВторой абзац" and tx == "Скопировано" and not menu(p), f"{eng} tap Copy: full text + toast, menu closed", (cp, tx))
        p.wait_for_timeout(2800)
        hold(p, ME, 600); p.locator(".lpm button").click(); p.wait_for_timeout(300)
        cp = p.evaluate("window.__cp")
        check(cp[-1].split(":", 1)[1] == "мой вопрос\nвторая строка", f"{eng} own message copies its text", cp[-1])
        hold(p, AG, 600); p.touchscreen.tap(200, 40); p.wait_for_timeout(200)
        check(not menu(p), f"{eng} outside tap closes")
        hold(p, AG, 600); p.keyboard.press("Escape"); check(not menu(p), f"{eng} Esc closes")
        hold(p, AG, 600); p.evaluate("document.querySelector('#s-body')&&document.querySelector('#s-body').dispatchEvent(new Event('scroll'));window.dispatchEvent(new Event('scroll'));document.dispatchEvent(new Event('scroll'))"); p.wait_for_timeout(100)
        check(not menu(p), f"{eng} scroll closes")
        p.evaluate("()=>{const g=document.createElement('div');g.className='me pending';g.textContent='призрак';document.querySelector('#s-hist').appendChild(g)}")
        hold(p, '#s-hist .me.pending', 600); check(not menu(p), f"{eng} ghost .pending: no menu")
        p.evaluate("document.querySelector('#s-hist .me.pending').remove()")
        sel = "()=>{const n=document.querySelector('%s');const r=document.createRange();r.selectNodeContents(n);const s=getSelection();s.removeAllRanges();s.addRange(r)}" % AG
        p.evaluate(sel); hold(p, AG, 600); check(not menu(p), f"{eng} existing selection: no menu")
        p.evaluate("getSelection().removeAllRanges()")
        ev(p, AG, "pointerdown"); p.evaluate(sel); p.wait_for_timeout(600); ev(p, AG, "pointerup")
        check(not menu(p), f"{eng} selection made during hold: no menu")
        p.evaluate("getSelection().removeAllRanges()")
        check(not errs, f"{eng} no page errors", errs); b.close()
finally:
    srv.terminate()
print("TOTAL", "PASS" if not fails else "FAIL"); sys.exit(1 if fails else 0)
