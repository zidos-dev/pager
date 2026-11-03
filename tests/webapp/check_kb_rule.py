"""Клавиатура над слоем (38c9994): пока поле сессии в фокусе, список под слоем скрыт; без фокуса и на списке — виден.
Правило: html.locked:has(:is(textarea,input):focus) #list-screen{visibility:hidden}."""
import os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).parent; PORT=int(os.environ.get("PORT","18986")); U=f"http://127.0.0.1:{PORT}"
data=Path(tempfile.gettempdir())/f"kb-rule-{PORT}.json"; data.write_text((ROOT.parent/"stand"/"demo2.json").read_text())
srv=subprocess.Popen([sys.executable,str(ROOT/"stand_server.py")],env=dict(os.environ,DATA_FILE=str(data),PORT=str(PORT)),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U+"/api/sessions",timeout=.3).close(); break
    except OSError: time.sleep(.1)
VIS="()=>getComputedStyle(document.getElementById('list-screen')).visibility"
fails=0
try:
  with sync_playwright() as pw:
    for eng in ("chromium","webkit"):
        b=getattr(pw,eng).launch(); ctx=b.new_context(viewport={"width":390,"height":844},locale="en-US",has_touch=True)
        ctx.route("https://telegram.org/**",lambda r:r.fulfill(body="")); p=ctx.new_page(); errs=[]; p.on("pageerror",lambda e:errs.append(str(e)))
        p.goto(U+"/app/"); p.wait_for_timeout(1500)
        p.locator("#list .card[data-id]").first.click(); p.wait_for_timeout(1500)
        seen=[p.evaluate(VIS)]                                   # сессия открыта, поле не в фокусе
        p.locator("#s-text").focus(); p.wait_for_timeout(200); seen.append(p.evaluate(VIS))
        p.locator("#s-text").blur(); p.wait_for_timeout(200); seen.append(p.evaluate(VIS))
        p.keyboard.press("Escape"); p.wait_for_timeout(1500); seen.append(p.evaluate(VIS))  # обратно на список
        ok=seen==["visible","hidden","visible","visible"] and not errs
        fails+=0 if ok else 1
        print(("PASS" if ok else "FAIL"),eng,f"visibility {seen} errs={errs[:2]}"); b.close()
finally:
    srv.terminate()
print("TOTAL", "FAIL" if fails else "PASS"); sys.exit(1 if fails else 0)
