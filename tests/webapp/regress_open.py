"""Регресс: раскрытие/закрытие сессии (слои) + опрос списка не оставляет следов на карточках (кеш listHTML)."""
import os, sys, json, time, subprocess, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).parent; PORT=int(os.environ.get("PORT","18985")); U=f"http://127.0.0.1:{PORT}"
data=Path(os.environ.get("TMPDIR","/tmp"))/f"regress-{PORT}.json"; data.write_text((ROOT.parent/"stand"/"demo2.json").read_text())
srv=subprocess.Popen([sys.executable,str(ROOT/"stand_server.py")],env=dict(os.environ,DATA_FILE=str(data),PORT=str(PORT)),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U+"/api/sessions",timeout=.3).close(); break
    except OSError: time.sleep(.1)
JS="""()=>[...document.querySelectorAll('#list .card')].map(c=>{const cw=c.closest('.cw')||c;return {id:c.dataset.id,cls:c.className+' '+cw.className,st:(c.getAttribute('style')||'')+(cw.getAttribute('style')||''),vis:c.getBoundingClientRect().height>0&&getComputedStyle(c).visibility!=='hidden'&&+getComputedStyle(c).opacity>0.99}})"""
fails=0
try:
  with sync_playwright() as pw:
    for eng in ("chromium","webkit"):
        b=getattr(pw,eng).launch(); ctx=b.new_context(viewport={"width":390,"height":844},locale="en-US")
        ctx.route("https://telegram.org/**",lambda r:r.fulfill(body="")); p=ctx.new_page(); errs=[]; p.on("pageerror",lambda e:errs.append(str(e)))
        p.goto(U+"/app/"); p.wait_for_timeout(1500)
        before=p.evaluate(JS)
        for rnd in range(2):
            target=[c for c in before if c["id"]!="a1c3e5f7"][rnd]["id"]
            p.locator(f'#list .card[data-id="{target}"]').click(); p.wait_for_timeout(1500)
            ok_open=p.locator("#session-screen").is_visible()
            p.keyboard.press("Escape"); p.wait_for_timeout(1500)
            p.wait_for_timeout(10000)  # опрос списка
            after=p.evaluate(JS)
            bad=[c for c in after if (not c["vis"]) or ("lo-" in c["cls"]) or ("transform" in c["st"] and "none" not in c["st"]) or ("opacity" in c["st"])]
            ok=ok_open and len(after)==len(before) and not bad and not errs
            fails+=0 if ok else 1
            print(("PASS" if ok else "FAIL"),eng,f"round{rnd+1} open={ok_open} cards {len(before)}->{len(after)} bad={bad[:2]} errs={errs[:2]}")
        p.screenshot(path=str(ROOT/f"regress-{eng}-{os.environ.get('TAG','x')}.png")); b.close()
finally:
    srv.terminate()
print("TOTAL", "FAIL" if fails else "PASS"); sys.exit(1 if fails else 0)
