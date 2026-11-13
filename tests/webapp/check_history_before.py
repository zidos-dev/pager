"""«Показать раньше» in the session feed (UX audit P2 №6).
1. The server window is the last 24 of 60 entries; the capsule is shown, a tap loads the 24 before it above, without a
   scroll jump (the first visible node stays put ±2 px); the next tap loads the rest and the capsule disappears.
2. Two polls later the loaded older entries are still there (the poll window does not evict them), and a selection in an
   older node survives a poll and a window slide.
3. Another session resets the loaded entries.
Run: python3 tests/webapp/check_history_before.py   (own stand on PORT, default 19143; Chromium + WebKit, 393×659 touch)"""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).parent; PORT = int(os.environ.get("PORT", "19143")); U = f"http://127.0.0.1:{PORT}"
data = Path(tempfile.gettempdir()) / f"before-{PORT}.json"
SID = "c3e5a7b9"; OTHER = "b2d4f6a8"
base = json.loads((ROOT.parent / "stand" / "demo2.json").read_text())
def ts(i): return "2026-11-12T%02d:%02d:00Z" % (10 + i // 60, i % 60)
allm = [{"role": "assistant", "uuid": f"h{i}", "ts": ts(i), "text": f"запись {i}: " + "длинный текст ленты, " * 6} for i in range(60)]
def publish():  # the stand serves window = last 24 of history_all, and pages back by ?before=
    d = json.loads(json.dumps(base)); d["session"][SID]["history_all"] = allm; data.write_text(json.dumps(d))
publish()
srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, DATA_FILE=str(data), PORT=str(PORT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U + "/api/sessions", timeout=.3).close(); break
    except OSError: time.sleep(.1)
fails = 0
def check(ok, name, detail=""):
    global fails; fails += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)
KEYS = "()=>[...document.querySelectorAll('#s-hist > .it[data-k]')].map(e=>e.dataset.k).filter(k=>/^[hn]\\d+$/.test(k))"
FIRSTVIS = """()=>{const b=document.querySelector('#s-body'),t=b.getBoundingClientRect().top;
 const e=[...document.querySelectorAll('#s-hist > .it[data-k]')].find(c=>!c.dataset.k.startsWith('t:')&&c.getBoundingClientRect().bottom>t);return {k:e.dataset.k,top:e.getBoundingClientRect().top-t}}"""
try:
  with sync_playwright() as pw:
    for eng in ("chromium", "webkit"):
        allm[:] = allm[:60]; publish()
        b = getattr(pw, eng).launch(); ctx = b.new_context(viewport={"width": 393, "height": 659}, is_mobile=True, has_touch=True, locale="ru-RU")
        p = ctx.new_page(); errs = []; p.on("pageerror", lambda e: errs.append(str(e)))
        p.goto(U + "/app/"); p.wait_for_timeout(1500)
        p.locator(f'#list .card[data-id="{SID}"]').click(); p.wait_for_timeout(1800)
        keys = p.evaluate(KEYS)
        check(keys == [f"h{i}" for i in range(36, 60)], f"{eng} window = last 24", keys[:2])
        btn = p.locator("#s-older"); check(btn.is_visible() and btn.inner_text() == "Показать раньше", f"{eng} capsule shown", btn.inner_text())
        # scroll to the top, remember the first visible node, tap
        p.evaluate("document.querySelector('#s-body').scrollTop=0"); p.wait_for_timeout(300)
        before = p.evaluate(FIRSTVIS)
        btn.scroll_into_view_if_needed(); btn.click(); p.wait_for_timeout(1200)
        keys = p.evaluate(KEYS)
        check(keys == [f"h{i}" for i in range(12, 60)], f"{eng} 24 older entries inserted above", [keys[0], len(keys)])
        after = p.evaluate("(k)=>{const b=document.querySelector('#s-body'),t=b.getBoundingClientRect().top,e=document.querySelector(`#s-hist > .it[data-k='${k}']`);return e.getBoundingClientRect().top-t}", before["k"])
        check(abs(after - before["top"]) <= 2, f"{eng} first visible node stays put", {"before": before, "after": after})
        # a selection in an older node, then two polls and a window slide
        sel = p.evaluate("""()=>{const el=document.querySelector('#s-hist [data-k="h20"] .msg');window.__n=el;el.scrollIntoView({block:'center'});
          const r=document.createRange();r.setStart(el.firstChild,3);r.setEnd(el.firstChild,30);const s=getSelection();s.removeAllRanges();s.addRange(r);return s.toString().length}""")
        allm.append({"role": "assistant", "uuid": "n60", "ts": ts(60), "text": "новая запись 60: " + "текст, " * 8}); publish()
        p.wait_for_timeout(9000)
        st = p.evaluate("()=>({len:getSelection().toString().length,conn:!!window.__n&&window.__n.isConnected,inside:!!window.__n&&window.__n.contains(getSelection().anchorNode)})")
        check(st == {"len": sel, "conn": True, "inside": True}, f"{eng} selection in an older node survives polls and a window slide", st)
        p.evaluate("getSelection().removeAllRanges()"); p.wait_for_timeout(3500)
        keys = p.evaluate(KEYS)
        check(keys == [f"h{i}" for i in range(12, 60)] + ["n60"], f"{eng} older entries kept after polls, new one arrived (h36 left the window, stays)", [keys[0], keys[-2:], len(keys)])
        # the rest
        p.evaluate("document.querySelector('#s-body').scrollTop=0"); p.wait_for_timeout(300)
        p.locator("#s-older").click(); p.wait_for_timeout(1200)
        keys = p.evaluate(KEYS)
        check(keys == [f"h{i}" for i in range(60)] + ["n60"] and p.locator("#s-older").is_hidden(), f"{eng} second tap loads the rest, capsule gone", [keys[0], len(keys)])
        # another session resets
        p.keyboard.press("Escape"); p.wait_for_timeout(1500)
        p.locator(f'#list .card[data-id="{OTHER}"]').click(); p.wait_for_timeout(1500)
        check(p.evaluate(KEYS) == [] and p.locator("#s-older").is_hidden(), f"{eng} other session shows none of them")
        p.keyboard.press("Escape"); p.wait_for_timeout(1500)
        p.locator(f'#list .card[data-id="{SID}"]').click(); p.wait_for_timeout(1800)
        keys = p.evaluate(KEYS)
        check(keys == [f"h{i}" for i in range(37, 60)] + ["n60"] and p.locator("#s-older").is_visible(), f"{eng} back to the first: window only again", [keys[0], len(keys)])
        check(not errs, f"{eng} no page errors", errs)
        b.close()
finally:
    srv.terminate()
print("TOTAL", "PASS" if not fails else "FAIL"); sys.exit(1 if fails else 0)
