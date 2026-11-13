"""Send failure: no ghost, text back in the field, toast with «Повторить»; retry (server restored) = exactly one message.
Run: python3 tests/webapp/check_send_fail.py   (own stand on PORT, default 19153; Chromium + WebKit)"""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).parent; PORT = int(os.environ.get("PORT", "19153")); U = f"http://127.0.0.1:{PORT}"
data = Path(tempfile.gettempdir()) / f"sendfail-{PORT}.json"; data.write_text((ROOT.parent / "stand" / "demo2.json").read_text())
log = Path(tempfile.gettempdir()) / f"sendfail-{PORT}.log"
A = "c3e5a7b9"; T = "retry check"
srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, DATA_FILE=str(data), PORT=str(PORT), SEND_LOG=str(log)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U + "/api/sessions", timeout=.3).close(); break
    except OSError: time.sleep(.1)
fails = 0
def check(ok, name, detail=""):
    global fails; fails += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)
def val(p): return p.evaluate("document.getElementById('s-text').value")
def sent(): return sum(1 for l in log.read_text().splitlines() if T in l) if log.exists() else 0
try:
  with sync_playwright() as pw:
    for eng in os.environ.get("ENGINES", "chromium,webkit").split(","):
      for mode in ("abort", "500", "lost"):
        log.write_text("")
        b = getattr(pw, eng).launch(); ctx = b.new_context(viewport={"width": 393, "height": 659}, is_mobile=True, has_touch=True)
        p = ctx.new_page(); errs = []; p.on("pageerror", lambda e: errs.append(str(e)))
        p.goto(U + f"/app/#s={A}"); p.wait_for_timeout(1800)
        if mode == "abort": p.evaluate("()=>{window.__f=fetch;window.fetch=(u,o)=>/\\/send$/.test(u)?Promise.reject(new TypeError('offline')):window.__f(u,o)}")  # page.route abort gave no failure in WebKit
        elif mode == "lost": p.evaluate("()=>{window.__f=fetch;window.fetch=(u,o)=>/\\/send$/.test(u)?window.__f(u,o).then(()=>{throw new TypeError('reply lost')}):window.__f(u,o)}")  # the server got it, the reply is lost
        else: ctx.add_cookies([{"name": "send_reply", "value": "fail", "url": U}])
        p.fill("#s-text", T); p.click("#s-send"); p.wait_for_timeout(2500)
        tag = f"{eng}/{mode}"
        check(p.locator("#s-hist .me.pending").count() == 0, f"{tag} no ghost left")
        check(val(p) == T, f"{tag} text back in the field", val(p))
        btn = p.locator("#toast button.ta")
        check(btn.count() == 1 and btn.inner_text() in ("Retry", "Повторить"), f"{tag} toast has the Retry action")
        if mode in ("abort", "lost"): p.evaluate("()=>{window.fetch=window.__f}")
        else: ctx.clear_cookies()
        if mode == "lost":  # the retry must carry the first request's key: the real server drops it (test_send_key_makes_a_retry_idempotent)
            p.evaluate("()=>{const b=document.querySelector('#toast button.ta');b.click();b.click()}"); p.wait_for_timeout(2500)
            keys = [json.loads(json.loads(l)["body"]).get("key") for l in log.read_text().splitlines() if T in l]
            check(len(keys) == 2 and keys[0] and keys[0] == keys[1], f"{tag} retry after a lost reply reuses the key", keys)
            check(val(p) == "", f"{tag} field empty after retry", val(p))
            check(not errs, f"{tag} no page errors", errs)
            b.close(); continue
        log.write_text("")
        p.evaluate("()=>{const b=document.querySelector('#toast button.ta');b.click();b.click()}"); p.wait_for_timeout(2500)
        check(sent() == 1, f"{tag} retry sends exactly one message", sent())
        check(val(p) == "", f"{tag} field empty after retry", val(p))
        check(not errs, f"{tag} no page errors", errs)
        b.close()
finally:
    srv.terminate(); srv.wait()
print("FAILS", fails); sys.exit(1 if fails else 0)
