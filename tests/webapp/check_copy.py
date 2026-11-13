"""Copy buttons in the session feed (UX audit P2 №5): one under every agent reply, one on every code block.
1. A tap puts the exact plain text into the clipboard (no button text), the toast «Скопировано» shows.
2. Without navigator.clipboard (Telegram WebView) the textarea + execCommand path still copies.
3. Polling does not duplicate the buttons; the buttons are ≥44×44, user-select:none, labelled; .msg stays selectable.
Run: python3 tests/webapp/check_copy.py   (own stand on PORT, default 19142; Chromium + WebKit, 393×659 touch)"""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).parent; PORT = int(os.environ.get("PORT", "19142")); U = f"http://127.0.0.1:{PORT}"
data = Path(tempfile.gettempdir()) / f"copy-{PORT}.json"
SID = "c3e5a7b9"
base = json.loads((ROOT.parent / "stand" / "demo2.json").read_text())
CODE = "a = 1\nb = 'x'"
hist = [{"role": "user", "uuid": "u1", "text": "покажи код"},
        {"role": "assistant", "uuid": "a1", "html": "<p>Первый ответ</p>"},
        {"role": "assistant", "uuid": "a2", "html": "<p>Вот код:</p><pre><code>a = 1\nb = &#x27;x&#x27;</code></pre><p>Готово.</p>"},
        {"role": "assistant", "uuid": "a3", "html": "<p>Два</p><pre><code>x</code></pre><pre><code>y</code></pre>"}]
d = json.loads(json.dumps(base)); d["session"][SID]["history"] = hist; data.write_text(json.dumps(d))
srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, DATA_FILE=str(data), PORT=str(PORT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U + "/api/sessions", timeout=.3).close(); break
    except OSError: time.sleep(.1)
fails = 0
def check(ok, name, detail=""):
    global fails; fails += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)
# records what a copy writes: the async API (stubbed where the engine denies clipboard permissions) and the legacy copy event
HOOK = """()=>{window.__cp=[];const w=navigator.clipboard&&navigator.clipboard.writeText?navigator.clipboard.writeText.bind(navigator.clipboard):null;
 if(w)navigator.clipboard.writeText=t=>{window.__cp.push('async:'+t);return w(t)};document.addEventListener('copy',()=>{const a=document.activeElement;window.__cp.push('legacy:'+(a&&a.value!=null?a.value.slice(a.selectionStart,a.selectionEnd):getSelection().toString()))},true)}"""
def open_feed(p):
    p.goto(U + "/app/"); p.wait_for_timeout(1500)
    p.locator(f'#list .card[data-id="{SID}"]').click(); p.wait_for_timeout(1800)
def read_clip(p, eng):  # the real clipboard where the engine lets us read it, else what the page wrote
    try: return p.evaluate("navigator.clipboard.readText()")
    except Exception: return (lambda cp: cp[-1].split(":", 1)[1] if cp else None)(p.evaluate("window.__cp"))
try:
  with sync_playwright() as pw:
    for eng in ("chromium", "webkit"):
        b = getattr(pw, eng).launch(); ctx = b.new_context(viewport={"width": 393, "height": 659}, is_mobile=True, has_touch=True, locale="ru-RU")
        if eng == "chromium": ctx.grant_permissions(["clipboard-read", "clipboard-write"])
        p = ctx.new_page(); errs = []; p.on("pageerror", lambda e: errs.append(str(e)))
        p.add_init_script("window.__stub=[]")
        open_feed(p); p.evaluate(HOOK)
        n = p.evaluate("()=>({msg:document.querySelectorAll('#s-hist .cpy[data-cp=msg]').length,pre:document.querySelectorAll('#s-hist .cpy[data-cp=pre]').length,users:document.querySelectorAll('#s-hist .me .cpy').length})")
        check(n == {"msg": 3, "pre": 3, "users": 0}, f"{eng} one button per agent reply and per code block", n)
        geo = p.evaluate("()=>[...document.querySelectorAll('#s-hist .cpy')].map(b=>{const r=b.getBoundingClientRect(),c=getComputedStyle(b);return {w:r.width,h:r.height,us:c.userSelect||c.webkitUserSelect,al:b.getAttribute('aria-label'),op:c.opacity}})")
        check(all(g["w"] >= 44 and g["h"] >= 44 and g["us"] == "none" and g["al"] and g["op"] == "1" for g in geo), f"{eng} buttons: ≥44×44, user-select:none, aria-label, visible without hover", geo[:2])
        # 1. the pre of the second reply
        p.locator('#s-hist [data-k="a2"] .cpy[data-cp=pre]').click(); p.wait_for_timeout(300)
        tx = p.evaluate("document.querySelector('#toast').textContent"); vis = p.evaluate("!document.querySelector('#toast').hidden")
        check(vis and tx == "Скопировано", f"{eng} toast after copy", tx)
        clip = read_clip(p, eng)
        check(clip == CODE, f"{eng} code block copies exactly its text", repr(clip))
        # 2. the whole reply: text of the block + paragraphs, no button text, no 'Копировать'
        p.wait_for_timeout(2800)
        p.locator('#s-hist [data-k="a2"] .cpy[data-cp=msg]').click(); p.wait_for_timeout(300)
        clip = read_clip(p, eng)
        check(clip is not None and "Вот код:" in clip and CODE in clip and "Готово." in clip and "Копировать" not in clip, f"{eng} reply copies its plain text without button text", repr(clip))
        p.wait_for_timeout(2800)
        # 3. no navigator.clipboard (Telegram WebView): the legacy path
        p.evaluate("()=>{Object.defineProperty(navigator,'clipboard',{value:undefined,configurable:true});window.__cp=[]}")
        p.locator('#s-hist [data-k="a1"] .cpy[data-cp=msg]').click(); p.wait_for_timeout(300)
        leg = p.evaluate("window.__cp")
        check(leg == ["legacy:Первый ответ"] and p.evaluate("document.querySelector('#toast').textContent") == "Скопировано", f"{eng} fallback without navigator.clipboard", leg)
        check(p.evaluate("document.querySelectorAll('textarea[readonly]').length") == 0, f"{eng} fallback textarea is removed")
        # 4. polling does not duplicate buttons
        p.wait_for_timeout(9500)
        n2 = p.evaluate("()=>({msg:document.querySelectorAll('#s-hist .cpy[data-cp=msg]').length,pre:document.querySelectorAll('#s-hist .cpy[data-cp=pre]').length})")
        check(n2 == {"msg": 3, "pre": 3}, f"{eng} two polls later no duplicate buttons", n2)
        us = p.evaluate("()=>{const c=getComputedStyle(document.querySelector('#s-hist .msg'));return c.userSelect||c.webkitUserSelect}")
        check(us == "text", f"{eng} .msg stays selectable", us)
        check(not errs, f"{eng} no page errors", errs)
        b.close()
finally:
    srv.terminate()
print("TOTAL", "PASS" if not fails else "FAIL"); sys.exit(1 if fails else 0)
