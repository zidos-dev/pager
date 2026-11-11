"""A: «Данные на» = время файла строк, не новее Claude; B: открытие листа при старых строках шлёт POST quota/refresh один раз, при свежих — нет;
D: метка «текущая версия» только у сессии, на которую указывает into старой версии.
Run: python3 tests/webapp/check_quota_stale.py   (BROWSER=chromium|webkit|оба по умолчанию; свой стенд на PORT, по умолчанию 18984)"""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).parent; PORT = int(os.environ.get("PORT", "18984")); U = f"http://127.0.0.1:{PORT}"
ENGS = [os.environ["BROWSER"]] if os.environ.get("BROWSER") in ("chromium", "webkit") else ["chromium", "webkit"]
_demo = json.loads((ROOT.parent / "stand" / "demo2.json").read_text())
S = _demo["sessions"]["sessions"]
for r in S:
    if r.get("group") == "wait" or r.get("perm"): r["group"] = "work"; r["perm"] = False
    r["current"] = False
S[0]["current"] = True; S[1]["current"] = True  # both claim "current"; only S[1] has an old version pointing at it
S.append({"id": "old00001", "name": "Старая версия", "group": "old", "cwd": "/tmp", "into": {"id": S[1]["id"], "name": S[1]["name"]}, "current": False})
_data = Path(tempfile.gettempdir()) / f"quota-stale-{PORT}.json"; _data.write_text(json.dumps(_demo))
fails = 0
def check(ok, name, detail=""):
    global fails; fails += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)
def start(age):
    srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, PORT=str(PORT), DATA_FILE=str(_data), QUOTA_FILE_AGE=str(age)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try: urllib.request.urlopen(U + "/api/sessions", timeout=.3).close(); break
        except OSError: time.sleep(.1)
    return srv
def hm(ts): return time.strftime("%H:%M", time.localtime(ts))
try:
  with sync_playwright() as pw:
    for eng in ENGS:
      for age in (7200, 100):
        srv = start(age)
        try:
            b = getattr(pw, eng).launch(); ctx = b.new_context(viewport={"width": 393, "height": 659}, has_touch=True, service_workers="block", locale="ru-RU")
            ctx.add_init_script("try{localStorage['cc-lang']='ru'}catch(e){}")
            p = ctx.new_page(); errs = []; posts = []; p.on("pageerror", lambda e: errs.append(str(e)))
            p.on("request", lambda r: posts.append(r.url) if r.method == "POST" and r.url.endswith("/api/quota/refresh") else None)
            p.goto(U + "/app/"); p.wait_for_timeout(1500)
            if age == 7200:
                cur = p.locator(".tag.cur"); n = cur.count()
                check(n == 1, f"{eng}: D one 'current version' tag, only on the card an old version points at", str(n))
                if n: check(cur.first.locator("xpath=ancestor::*[contains(@class,'card') or @data-id or self::button or self::a][1]").inner_text().find(S[1]["name"].split(" №")[0]) >= 0, f"{eng}: D the tag sits on the pointed-at session")
            t0 = time.time()
            p.click("#lab-q"); p.wait_for_timeout(1500)
            n = len(posts)
            if age == 7200:
                check(n == 1, f"{eng}: B stale rows -> exactly one background POST on open", str(n))
                # after the refresh the file is fresh again (stand rewrites it): label shows ~4 min ago, not Claude's time
                want = hm(t0 - 240)
                txt = p.locator("#q-at").inner_text()
                check(want in txt or hm(t0 - 239) in txt or hm(t0 - 241) in txt, f"{eng}: A label is the file time, not Claude's newer one", txt)
            else:
                check(n == 0, f"{eng}: B fresh rows -> no POST on open", str(n))
                txt = p.locator("#q-at").inner_text()
                check(hm(t0 - 100) in txt or hm(t0 - 101) in txt, f"{eng}: A label is the file time (100 s) although Claude is 30 s old", txt)
            check(p.locator("#q-rows .qr").count() == 6, f"{eng}: rows still shown", str(p.locator("#q-rows .qr").count()))
            check(not errs, f"{eng}: no page errors", str(errs))
            b.close()
        finally:
            srv.kill(); srv.wait()
finally:
    _data.unlink(missing_ok=True)
print("FAILS", fails); sys.exit(1 if fails else 0)
