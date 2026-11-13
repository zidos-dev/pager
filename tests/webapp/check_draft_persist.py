"""Draft text survives reload (localStorage per session); other session empty; cleared after send; storage blocked = no errors.
Run: python3 tests/webapp/check_draft_persist.py   (own stand on PORT, default 19152; Chromium + WebKit)"""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).parent; PORT = int(os.environ.get("PORT", "19152")); U = f"http://127.0.0.1:{PORT}"
data = Path(tempfile.gettempdir()) / f"draft-{PORT}.json"; data.write_text((ROOT.parent / "stand" / "demo2.json").read_text())
A, B = "c3e5a7b9", "a1c3e5f7"
srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, DATA_FILE=str(data), PORT=str(PORT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U + "/api/sessions", timeout=.3).close(); break
    except OSError: time.sleep(.1)
fails = 0
def check(ok, name, detail=""):
    global fails; fails += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)
def val(p): return p.evaluate("$('s-text').value") if False else p.evaluate("document.getElementById('s-text').value")
try:
  with sync_playwright() as pw:
    for eng in os.environ.get("ENGINES", "chromium,webkit").split(","):
        b = getattr(pw, eng).launch(); ctx = b.new_context(viewport={"width": 393, "height": 659}, is_mobile=True, has_touch=True)
        p = ctx.new_page(); errs = []; p.on("pageerror", lambda e: errs.append(str(e)))
        p.goto(U + f"/app/#s={A}"); p.wait_for_timeout(1800)
        p.fill("#s-text", "черновик номер раз"); p.wait_for_timeout(600)
        p.reload(); p.wait_for_timeout(1800)
        check(val(p) == "черновик номер раз", f"{eng} draft restored after reload", val(p))
        p.goto(U + f"/app/#s={B}"); p.reload(); p.wait_for_timeout(1800)
        check(val(p) == "", f"{eng} other session is empty", val(p))
        p.goto(U + f"/app/#s={A}"); p.reload(); p.wait_for_timeout(1800)
        p.click("#s-send"); p.wait_for_timeout(1500)
        check(val(p) == "", f"{eng} field empty after send")
        p.reload(); p.wait_for_timeout(1800)
        check(val(p) == "", f"{eng} no draft after successful send", val(p))
        p.fill("#s-text", "x"); p.fill("#s-text", ""); p.wait_for_timeout(600)
        check(p.evaluate("localStorage.getItem('zp.draft.%s')" % A) is None, f"{eng} empty field clears the key")
        b.close()
        # хранилище бросает: поведение прежнее, ошибок нет
        b = getattr(pw, eng).launch(); ctx = b.new_context(viewport={"width": 393, "height": 659}); p = ctx.new_page(); errs = []; p.on("pageerror", lambda e: errs.append(str(e)))
        p.add_init_script("Storage.prototype.setItem=function(){throw new Error('blocked')};Storage.prototype.getItem=function(){throw new Error('blocked')}")
        p.goto(U + f"/app/#s={A}"); p.wait_for_timeout(1800); p.fill("#s-text", "abc"); p.wait_for_timeout(600); p.evaluate("dispatchEvent(new Event('pagehide'))")
        check(val(p) == "abc" and not errs, f"{eng} blocked storage: no errors, field intact", errs)
        b.close()
finally:
    srv.terminate(); srv.wait()
print("FAILS", fails); sys.exit(1 if fails else 0)
