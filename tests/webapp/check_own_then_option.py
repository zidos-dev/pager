"""Owner 13.11 01:21 (iPhone, «не выбирается ответ»): with «Свой ответ» open, a tap on an option did nothing (the
options were disabled) and iOS selected the option text instead. Now the tap closes the editor and answers the option;
the option text is not selectable.
Run: python3 tests/webapp/check_own_then_option.py   (own stand on PORT, default 18985; Chromium + WebKit, 393×659 touch)"""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).parent; PORT = int(os.environ.get("PORT", "18985")); U = f"http://127.0.0.1:{PORT}"
SID = "a1c3e5f7"; OPEN = '.lab-ask-root[data-state="open"]'
tmp = Path(tempfile.mkdtemp()); data, log = tmp / "data.json", tmp / "answers.jsonl"
base = (ROOT.parent / "stand" / "demo2.json").read_text()
srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, DATA_FILE=str(data), ANSWER_LOG=str(log), PORT=str(PORT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
fails = 0
def check(ok, name, detail=""):
    global fails; fails += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)
try:
  data.write_text(base)
  for _ in range(50):
    try: urllib.request.urlopen(U + "/api/sessions", timeout=.3).close(); break
    except OSError: time.sleep(.1)
  with sync_playwright() as pw:
    for eng in ("chromium", "webkit"):
        data.write_text(base); log.write_text("")
        b = getattr(pw, eng).launch(); ctx = b.new_context(viewport={"width": 393, "height": 659}, is_mobile=True, has_touch=True)
        p = ctx.new_page(); errs = []; p.on("pageerror", lambda e: errs.append(str(e)))
        p.goto(U + "/app/"); p.wait_for_timeout(1500)
        p.locator(f'.card[data-id="{SID}"]').click(); root = p.locator(OPEN); root.wait_for(timeout=8000)
        root.locator('[data-act="own"]').click(); p.wait_for_timeout(500)
        check(root.locator(".lab-ask-own-text").is_visible(), f"{eng} own editor open")
        opt = root.locator(".lab-ask-opt").nth(1)
        check(opt.is_enabled(), f"{eng} options stay enabled with the editor open")
        us = opt.evaluate("e=>{const c=getComputedStyle(e);return c.webkitUserSelect||c.userSelect}")
        check(us == "none", f"{eng} option text is not selectable", us)
        opt.tap(); p.wait_for_timeout(1500)
        posts = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
        check([x.get("action") for x in posts] == ["option"] and posts[0].get("index") == 2, f"{eng} tap answers the option", posts)
        check(not errs, f"{eng} no page errors", errs)
        b.close()
finally:
    srv.terminate()
print("TOTAL", "PASS" if not fails else "FAIL"); sys.exit(1 if fails else 0)
