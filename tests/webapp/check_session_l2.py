"""Open session screen (lane l2): quote in the question sheet, pill vs feed end, multi-line composer,
«What's happening» card, jump-to-end button, terminal quick keys, «N more waiting» strip.

Usage: PORT=19321 python3 check_session_l2.py   (own stand on a temp copy of tests/stand/demo2.json; chromium + webkit)
Env: SHOTS=dir — also writes frames; ONLY=1,3 — run only those items.
"""
import json, os, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

APP = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("PORT", "19321")); URL = f"http://127.0.0.1:{PORT}"
SHOTS = os.environ.get("SHOTS"); ONLY = set(os.environ.get("ONLY", "").split(",")) - {""}
LONG = ("Before I touch anything: the session cache currently lives in process memory, so every restart drops all logins. "
        "There are three places it can go and each has a different failure mode. Redis is shared between instances and survives restarts, "
        "but adds a network hop to every request and one more thing to page about at night. An in-memory LRU is the simplest and fastest, "
        "yet each instance has its own copy, so a user bounced between instances gets logged out. A Postgres table is durable and already "
        "monitored, but it makes the hot path slower by a few milliseconds. One decision is yours: where should it live?")
SID_Q, SID_TERM, SID_WORK = "a1c3e5f7", "h2y8b1ef", "c3e5a7b9"
bad = 0


def check(name, ok, info=""):
    global bad
    bad += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + name, info)


def mkdata(waiting_only_this=False):
    d = json.loads((APP / "tests/stand/demo2.json").read_text())
    h = d["session"][SID_Q]["history"]
    h[-1] = {"role": "assistant", "uuid": "last-long", "ts": "2026-10-14T09:59:00.000Z", "html": LONG, "files": []}
    hw = d["session"][SID_WORK]["history"]
    for i in range(40):
        hw.append({"role": "user" if i % 2 else "assistant", "uuid": f"w{i}", "ts": "2026-10-14T09:%02d:00.000Z" % (13 + i % 40),
                   "text": "Message number %d from the long thread, long enough to wrap onto a couple of lines on a phone." % i} if i % 2 else
                  {"role": "assistant", "uuid": f"w{i}", "ts": "2026-10-14T09:%02d:00.000Z" % (13 + i % 40), "html": "Reply number %d: checked the files, nothing else to change here, moving on to the next step of the plan." % i, "files": []})
    if waiting_only_this:
        for s in d["sessions"]["sessions"]:
            if s["id"] != SID_Q and s["group"] == "wait":
                s["group"], s["state"] = "done", "done"
    f = Path(tempfile.mkdtemp()) / "d.json"; f.write_text(json.dumps(d)); return f


def add_msgs(f, sid, n):
    d = json.loads(f.read_text()); h = d["session"][sid]["history"]
    for i in range(n): h.append({"role": "assistant", "uuid": f"new{len(h)}", "html": f"Fresh message {i + 1}", "files": []})
    f.write_text(json.dumps(d))


def start(f, extra=None):
    try: urllib.request.urlopen(URL + "/api/sessions", timeout=.3).close(); sys.exit(f"port {PORT} is already served by another stand: its data would be tested instead of ours")
    except OSError: pass
    env = dict(os.environ, DATA_FILE=str(f), PORT=str(PORT), **(extra or {}))
    srv = subprocess.Popen([sys.executable, str(APP / "tests/webapp/stand_server.py")], env=env, stdout=subprocess.DEVNULL)
    for _ in range(60):
        try: urllib.request.urlopen(URL + "/api/sessions", timeout=.3).close(); break
        except OSError: time.sleep(.1)
    return srv


def page(pw, eng, w, h, dark=False, ru=False):
    b = getattr(pw, eng).launch()
    ctx = b.new_context(viewport={"width": w, "height": h}, is_mobile=(eng == "chromium" and w < 800), has_touch=w < 800,
                        color_scheme="dark" if dark else "light")
    if ru: ctx.add_init_script("localStorage.setItem('cc-lang','ru')")
    p = ctx.new_page(); p.goto(URL + "/app/?theme=tg-light"); p.wait_for_selector("#list .card"); return b, p


def open_sess(p, sid):
    p.locator(f'#list .card[data-id="{sid}"]').click(); p.wait_for_selector("#s-hist .it", timeout=8000)
    if sid == SID_Q:
        try: p.wait_for_selector(".lab-ask-root[data-state=open]", timeout=6000)
        except Exception: pass
    p.wait_for_timeout(1100)


def shot(p, name):
    if SHOTS: Path(SHOTS).mkdir(parents=True, exist_ok=True); p.screenshot(path=f"{SHOTS}/{name}.png")


def run_item(n):
    return not ONLY or str(n) in ONLY


with sync_playwright() as pw:
    DATA = mkdata(); KEYLOG = str(DATA) + ".keys"; srv = start(DATA, {"KEY_LOG": KEYLOG})
    try:
        ORIG = DATA.read_text()
        for eng in ("chromium", "webkit"):
            DATA.write_text(ORIG)  # каждый движок — на свежих данных (F1 отвечает на вопрос)
            # ---- №1: quote in the question sheet (blur stays), narrow column on wide
            if run_item(1):
                for (w, h, dark) in ((390, 844, False), (393, 659, True), (1440, 900, False)):
                    b, p = page(pw, eng, w, h, dark); open_sess(p, SID_Q)
                    tag = f"№1 {eng} {w}x{h}{'d' if dark else ''}"
                    q = p.locator(".lab-ask-root[data-state=open] .lab-ask-quote")
                    check(tag + " quote exists", q.count() == 1, p.evaluate("()=>{const r=document.querySelector('.lab-ask-root');return r?r.dataset.state+' quotes='+document.querySelectorAll('.lab-ask-quote').length:'no root'}"))
                    if q.count() == 1:
                        m = p.evaluate("""()=>{const q=document.querySelector('.lab-ask-quote'),s=document.querySelector('.lab-ask-sheet').getBoundingClientRect(),
                          lh=parseFloat(getComputedStyle(q).lineHeight),r=q.getBoundingClientRect();
                          return {lines:Math.round((r.height-parseFloat(getComputedStyle(q).paddingTop)-parseFloat(getComputedStyle(q).paddingBottom))/lh),txt:q.textContent,sw:Math.round(s.width),top:Math.round(r.top),inside:r.top>=s.top&&r.bottom<=s.bottom,
                                  blur:getComputedStyle(document.querySelector('.lab-ask-scrim')).backdropFilter||getComputedStyle(document.querySelector('.lab-ask-scrim')).webkitBackdropFilter}}""")
                        check(tag + " 2–3 lines, last agent message", 2 <= m["lines"] <= 3 and "One decision is yours" in m["txt"], str(m["lines"]))
                        check(tag + " inside sheet", m["inside"])
                        check(tag + " blur kept", "blur" in (m["blur"] or "") or eng == "webkit", m["blur"])
                        h0 = q.bounding_box()["height"]; q.click(); p.wait_for_timeout(400)
                        h1 = q.bounding_box()["height"]
                        check(tag + " tap expands in place", h1 > h0 + 20 and q.get_attribute("aria-expanded") == "true", f"{h0:.0f}->{h1:.0f}")
                        opts = p.evaluate("()=>[...document.querySelectorAll('.lab-ask-opt')].every(o=>{const r=o.getBoundingClientRect();return r.bottom<=innerHeight+1})")
                        check(tag + " options stay in window", opts)
                        shot(p, f"l2-1-{eng}-{w}{'d' if dark else ''}")
                        q.click(); p.wait_for_timeout(300)
                        check(tag + " tap again collapses", q.bounding_box()["height"] < h1 - 20)
                        if w >= 1000: check(tag + " narrow column", m["sw"] <= 560, str(m["sw"]))
                    b.close()
            # ---- №7: pill does not lie on the last lines, no extra padding
            if run_item(7):
                for (w, h) in ((390, 844), (393, 659)):
                    b, p = page(pw, eng, w, h); open_sess(p, SID_Q); tag = f"№7 {eng} {w}x{h}"
                    p.mouse.click(w // 2, 40); p.wait_for_timeout(900)
                    p.evaluate("()=>{const b=document.getElementById('s-body');b.scrollTop=b.scrollHeight}"); p.wait_for_timeout(500)
                    g = p.evaluate("""()=>{const pill=document.querySelector('.lab-ask-pill').getBoundingClientRect(),items=[...document.querySelectorAll('#s-hist .it')],
                      l=items[items.length-1].getBoundingClientRect();return {gap:Math.round(pill.top-l.bottom),pt:Math.round(pill.top),lb:Math.round(l.bottom)}}""")
                    check(tag + " last item above pill, gap ≤24", 0 <= g["gap"] <= 24, str(g))
                    shot(p, f"l2-7-{eng}-{w}")
                    b.close()
            # ---- №8: multi-line composer
            if run_item(8):
                b, p = page(pw, eng, 390, 844); open_sess(p, SID_WORK); tag = f"№8 {eng}"
                p.locator("#s-text").fill("Первая строка указания\nвторая строка, чуть длиннее первой, чтобы шире\nтретья"); p.wait_for_timeout(300)
                g = p.evaluate("""()=>{const c=document.querySelector('#session-screen .compose'),t=c.querySelector('textarea').getBoundingClientRect(),cr=c.getBoundingClientRect(),
                  b=['s-slash','s-clip','s-send'].map(i=>document.getElementById(i).getBoundingClientRect());return {tw:Math.round(t.width),cw:Math.round(cr.width),
                  below:b.every(r=>r.top>=t.bottom-1),sendRight:Math.round(cr.right-b[2].right)}}""")
                check(tag + " text full width", g["tw"] >= g["cw"] - 24, str(g))
                check(tag + " buttons in the lower row", g["below"] and g["sendRight"] <= 12, str(g))
                shot(p, f"l2-8-{eng}")
                p.locator("#s-text").fill(""); p.wait_for_timeout(200)
                g2 = p.evaluate("()=>{const t=document.querySelector('#session-screen .compose textarea').getBoundingClientRect();const s=document.getElementById('s-send').getBoundingClientRect();return s.top<t.bottom}")
                check(tag + " empty field: single row again", g2)
                b.close()
            # ---- №9: «What's happening» only when it differs from the live line
            if run_item(9):
                b, p = page(pw, eng, 393, 659); open_sess(p, SID_WORK); tag = f"№9 {eng}"
                check(tag + " work: card hidden, live line shown", p.locator("#s-reason").is_hidden() and p.locator(".live").count() == 1)
                p.keyboard.press("Escape"); p.go_back() if False else None
                b.close()
                b, p = page(pw, eng, 393, 659); open_sess(p, SID_TERM)
                check(tag + " terminal wait: card shown", p.locator("#s-reason").is_visible())
                b.close()
            # ---- №10: jump-to-end button with a counter
            if run_item(10):
                b, p = page(pw, eng, 390, 844); open_sess(p, SID_WORK); tag = f"№10 {eng}"
                btn = p.locator("#s-tobot")
                check(tag + " hidden at the end", btn.is_hidden())
                p.mouse.move(195, 400); p.mouse.wheel(0, -9000); p.wait_for_timeout(900)  # колесо — как палец: сбрасывает своё авто-удержание низа
                check(tag + " shown when scrolled up", btn.is_visible())
                if btn.is_visible():
                    bb = btn.bounding_box(); comp = p.locator("#session-screen .compose").bounding_box()
                    check(tag + " 44×44 above the field", round(bb["width"]) == 44 and round(bb["height"]) == 44 and bb["y"] + 44 <= comp["y"], str(bb))
                    check(tag + " no counter yet", p.locator("#s-tobot .cnt").is_hidden() or p.locator("#s-tobot .cnt").count() == 0)
                    add_msgs(DATA, SID_WORK, 2); p.wait_for_timeout(4500)
                    check(tag + " counter = 2 new", p.locator("#s-tobot .cnt").count() == 1 and p.locator("#s-tobot .cnt").inner_text().strip() == "2", p.locator("#s-tobot").inner_text())
                    shot(p, f"l2-10-{eng}")
                    btn.click(); p.wait_for_timeout(1500)
                    end = p.evaluate("()=>{const b=document.getElementById('s-body');return b.scrollHeight-b.scrollTop-b.clientHeight}")
                    check(tag + " tap scrolls to the end, button gone", end < 30 and btn.is_hidden(), str(end))
                b.close()
            # ---- №3: terminal quick keys
            if run_item(3):
                b, p = page(pw, eng, 390, 844); open_sess(p, SID_TERM); tag = f"№3 {eng}"
                ks = p.locator("#s-keys [data-k]")
                check(tag + " y/n/⏎/Esc + Screen", ks.count() == 4 and p.locator("#s-screen").is_visible(), str(ks.count()))
                if ks.count() == 4:
                    ok = p.evaluate("()=>[...document.querySelectorAll('#s-keys button,#s-screen')].every(b=>{const r=b.getBoundingClientRect();return r.width>=44&&r.height>=44})")
                    check(tag + " targets ≥44", ok)
                    shot(p, f"l2-3-{eng}")
                    ks.nth(0).click(); p.wait_for_timeout(600)
                    sent = [json.loads(l) for l in Path(KEYLOG).read_text().splitlines()] if Path(KEYLOG).exists() else []
                    check(tag + " «y» sent to the session", sent and sent[-1] == {"sid": SID_TERM, "key": "y"}, str(sent[-1:]))
                    p.locator("#s-screen").click(); p.wait_for_timeout(700)
                    check(tag + " «Screen» opens the sheet with the screen", p.locator("#scr-sheet").is_visible() and "Proceed?" in p.locator("#scr-text").inner_text())
                    p.locator("#scr-close").click(); p.wait_for_timeout(500)
                b.close()
                b, p = page(pw, eng, 390, 844); open_sess(p, SID_WORK)
                check(tag + " not shown for a working session", p.locator("#s-keys").is_hidden())
                b.close()
            # ---- F1: «N more waiting · Next ›»
            if run_item("F1"):
                b, p = page(pw, eng, 390, 844); open_sess(p, SID_Q); tag = f"F1 {eng}"
                check(tag + " no strip before an answer", p.locator("#s-next").is_hidden())
                p.locator('.lab-ask-opt[data-k="0"]').click(); p.wait_for_timeout(3000)
                nx = p.locator("#s-next")
                check(tag + " strip after the answer", nx.is_visible() and "2 more waiting" in nx.inner_text(), nx.inner_text() if nx.count() else "")
                if nx.is_visible():
                    shot(p, f"l2-f1-{eng}")
                    nx.locator("button").click(); p.wait_for_timeout(1200)
                    cur = p.evaluate("()=>document.getElementById('s-name').textContent")
                    check(tag + " next waiting session opens", p.locator("#session-screen").is_visible() and "Refactor auth" not in cur, cur)
                b.close()
    finally:
        srv.terminate()
    # F1 negative: nobody else waits -> no strip
    DATA = mkdata(True); ORIG = DATA.read_text(); srv = start(DATA)
    try:
        for eng in ("chromium", "webkit"):
            DATA.write_text(ORIG)
            if run_item("F1"):
                b, p = page(pw, eng, 390, 844); open_sess(p, SID_Q)
                p.locator('.lab-ask-opt[data-k="0"]').click(); p.wait_for_timeout(3000)
                check(f"F1 {eng} no other waiting: no strip", p.locator("#s-next").is_hidden())
                b.close()
    finally:
        srv.terminate()

print("TOTAL", "FAIL" if bad else "PASS", bad); sys.exit(1 if bad else 0)
