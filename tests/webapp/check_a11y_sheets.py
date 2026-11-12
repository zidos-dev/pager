"""S14/S24: листы — role=dialog, aria-modal, фокус внутрь, фон inert, возврат фокуса вызвавшему; список сессий не в aria-live.
Run: PORT=25100 python3 tests/webapp/check_a11y_sheets.py   (свой стенд; Chromium + WebKit, 393×659)"""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).parent; PORT = int(os.environ.get("PORT", "25100")); U = f"http://127.0.0.1:{PORT}"
_demo = json.loads((ROOT.parent / "stand" / "demo2.json").read_text())
_data = Path(tempfile.gettempdir()) / f"a11y-sheets-{PORT}.json"; _data.write_text(json.dumps(_demo))
srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, PORT=str(PORT), DATA_FILE=str(_data)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U + "/api/sessions", timeout=.3).close(); break
    except OSError: time.sleep(.1)
fails = 0
def check(ok, name, detail=""):
    global fails; fails += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)
IN = "(id)=>{const s=document.getElementById(id);return s.contains(document.activeElement)}"
try:
  with sync_playwright() as pw:
    for eng in ("chromium", "webkit"):
        b = getattr(pw, eng).launch(); ctx = b.new_context(viewport={"width": 393, "height": 659}, has_touch=True, service_workers="block", locale="ru-RU")
        p = ctx.new_page(); errs = []; p.on("pageerror", lambda e: errs.append(str(e)))
        p.goto(U + "/app/"); p.wait_for_timeout(1500)
        check(p.get_attribute("#list", "aria-live") is None, f"{eng}: S24 the whole list is not aria-live")
        check(p.get_attribute("#live-note", "aria-live") == "polite", f"{eng}: S24 separate polite announcer exists")
        def open_check(label, opener_sel, sheet_id, via_menu=None):
            if via_menu:
                p.focus("#list-more"); p.keyboard.press("Enter"); p.wait_for_timeout(500)
                p.locator(f'.mn-row[data-k="{via_menu}"]').click(); opener = "list-more"
            else:
                p.focus(opener_sel); p.keyboard.press("Enter"); opener = opener_sel.lstrip("#")
            p.wait_for_timeout(900)
            check(p.get_attribute("#" + sheet_id, "role") == "dialog" and p.get_attribute("#" + sheet_id, "aria-modal") == "true", f"{eng}: {label} role=dialog aria-modal")
            lab = p.evaluate(f"(()=>{{const s=document.getElementById('{sheet_id}');const l=s.getAttribute('aria-labelledby');return (l&&document.getElementById(l)&&document.getElementById(l).textContent.trim())||s.getAttribute('aria-label')||''}})()")
            check(bool(lab), f"{eng}: {label} has an accessible name", lab)
            check(p.evaluate(IN, sheet_id), f"{eng}: {label} focus moved inside the sheet")
            check(p.evaluate("[...document.querySelectorAll('body > main')].every(m=>m.inert)"), f"{eng}: {label} background mains are inert")
            check(not p.evaluate(f"document.getElementById('{sheet_id}').inert"), f"{eng}: {label} sheet itself is not inert")
            return opener
        def close_check(label, opener, how):
            if how == "esc": p.keyboard.press("Escape")
            else: p.locator(".sheet:not([hidden]) .sx").click()
            p.wait_for_timeout(900)
            check(p.evaluate("[...document.querySelectorAll('body > main')].every(m=>!m.inert)"), f"{eng}: {label} inert released ({how})")
            check(p.evaluate(f"document.activeElement && document.activeElement.id === '{opener}'"), f"{eng}: {label} focus returns to the opener ({how})", p.evaluate("document.activeElement && (document.activeElement.id||document.activeElement.tagName)"))
        o = open_check("quota", "#lab-q", "quota-sheet"); close_check("quota", o, "esc")
        o = open_check("model", None, "model-sheet", "model"); close_check("model", o, "x")
        o = open_check("bots", None, "bots-sheet", "bots"); close_check("bots", o, "esc")
        check(not errs, f"{eng}: no page errors", str(errs))
        b.close()
finally:
    srv.terminate()
print("FAILS:", fails); sys.exit(1 if fails else 0)
