"""Lane l5: wide «New session» in the right pane, session address #s=<id>, offline line does not shift cards.

Chromium + WebKit. 390×844 and 393×659 (address, back, offline), 1440×900 (pane, hint, shortcuts).
Light and dark. Russian via localStorage cc-lang. Own stand; PORT / APP_DIR / SHOTS from the environment.

The suite is red on the pre-fix client: #new covers the list, the address stays on the first session,
the empty pane keeps its placeholder while someone is waiting, and the offline line pushes the cards.
"""
import json, os, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "22642"))
URL = f"http://127.0.0.1:{PORT}"
APP = Path(os.environ.get("APP_DIR", ROOT.parents[1] / "assets" / "webapp"))
SHOTS = Path(os.environ["SHOTS"]) if os.environ.get("SHOTS") else None
SID_A, SID_B = "a1c3e5f7", "b2d4f6a8"  # first waiting, the one «Next» opens
fails = 0


def check(ok, name, detail=""):
    global fails
    fails += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + name, detail, flush=True)


def shot(page, name):
    if not SHOTS:
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / name))


def boot_data():
    return json.loads((ROOT.parent / "stand" / "demo2.json").read_text())


def no_wait(d):
    for s in d["sessions"]["sessions"]:
        if s.get("group") == "wait":
            s["group"], s["state"] = "done", "done"
    return d


def start(data_path):
    env = dict(os.environ, DATA_FILE=str(data_path), PORT=str(PORT), APP_DIR=str(APP))
    srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(URL + "/api/sessions", timeout=.3).close()
            break
        except OSError:
            time.sleep(.1)
    else:
        srv.terminate()
        raise RuntimeError("stand did not start")
    return srv


def new_page(pw, eng, w, h, scheme, lang, ua=None):
    b = getattr(pw, eng).launch()
    kw = dict(viewport={"width": w, "height": h}, color_scheme=scheme, locale="ru-RU" if lang == "ru" else "en-US")
    if ua:
        kw["user_agent"] = ua
    ctx = b.new_context(**kw)
    ctx.add_init_script("try{localStorage.setItem('cc-lang','%s')}catch(e){}" % lang)
    p = ctx.new_page()
    errs = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    return b, p, errs


def wait_app(p):
    p.goto(URL + "/app/", wait_until="load")
    p.wait_for_selector("#list .card", timeout=8000)


def hash_sid(p):
    return p.evaluate("() => new URLSearchParams(location.hash.replace(/^#/, '')).get('s')")


def view(p):
    return p.evaluate("() => document.documentElement.dataset.view")


def hint_text(p, sel):
    return p.evaluate(
        """(sel) => { const e = document.querySelector(sel); if (!e) return '';
          const s = getComputedStyle(e); return s.display === 'none' ? '' : (e.textContent || '').trim(); }""",
        sel)


def run_wide(pw, eng, scheme, lang, ua=None):
    tag = f"wide {eng} {scheme} {lang}" + (" mac" if ua else "")
    b, p, errs = new_page(pw, eng, 1440, 900, scheme, lang, ua)
    try:
        wait_app(p)
        p.wait_for_timeout(600)
        # №12: empty pane opens the first waiting session; the list stays
        name = p.locator("#s-name").text_content() or ""
        check(view(p) == "session" and "Refactor auth" in name, tag + " first waiting opens", name)
        check(p.locator("#list-screen").is_visible() and p.locator(".pane-empty").evaluate(
            "e => getComputedStyle(e).display") == "none", tag + " list stays, placeholder hidden")
        lb = p.locator("#list-screen").bounding_box()
        sb = p.locator("#session-screen").bounding_box()
        check(lb and lb["width"] < 420 and sb and sb["x"] >= 380, tag + " session is the right pane",
              f"list {lb} session {sb}")
        shot(p, f"l5-pane-{eng}-{scheme}-{lang}.png")
        if view(p) == "session":
            p.reload(wait_until="load")
            p.wait_for_function("() => document.getElementById('s-name').textContent.includes('Refactor')", timeout=8000)
            p.wait_for_timeout(400)
            check("Refactor auth" in p.locator("#s-name").inner_text(), tag + " reload keeps the open session",
                  p.locator("#s-name").inner_text())
        else:
            check(False, tag + " reload keeps the open session", "session was not open")
        # another card: address follows, one history entry, reload opens that session
        n0 = p.evaluate("history.length")
        p.locator(f'#list .card[data-id="{SID_B}"]').click()
        p.wait_for_timeout(700)
        opened = p.locator("#s-name").inner_text() if p.locator("#session-screen").is_visible() else ""
        check(hash_sid(p) == SID_B and "release notes" in opened.lower(),
              tag + " address follows the other card", f"{hash_sid(p)} {opened}")
        check(p.evaluate("history.length") == n0 + 1, tag + " other card adds one history entry",
              str(p.evaluate("history.length") - n0))
        p.reload(wait_until="load")
        p.wait_for_timeout(700)
        opened = p.locator("#s-name").inner_text() if p.locator("#session-screen").is_visible() else ""
        check(hash_sid(p) == SID_B and "release notes" in opened.lower(),
              tag + " reload opens the card that was on screen", f"{hash_sid(p)} {opened}")
        # hint under the session field; both shortcuts send, plain Enter does not
        # Playwright WebKit's own UA is Macintosh, so the quiet caption is ⌘ there too
        mac = bool(ua) or bool(p.evaluate("() => /Macintosh|Mac OS X|iPhone|iPad/.test(navigator.userAgent)"))
        want = "⌘+Enter" if mac else "Ctrl+Enter"
        send_word = "отправить" if lang == "ru" else "send"
        ht = hint_text(p, "#s-hint")
        check(want in ht and send_word in ht.lower(), tag + " quiet send hint", ht)
        posts = []
        p.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "/send" in r.url else None)
        p.locator("#s-text").fill("hello")
        p.locator("#s-text").press("Enter")
        p.wait_for_timeout(350)
        check(not posts, tag + " Enter alone does not send", str(posts))
        p.locator("#s-text").press("Control+Enter")
        p.wait_for_timeout(500)
        check(len(posts) == 1, tag + " Ctrl+Enter sends", str(len(posts)))
        p.locator("#s-text").fill("meta")
        p.locator("#s-text").press("Meta+Enter")
        p.wait_for_timeout(500)
        check(len(posts) == 2, tag + " ⌘+Enter sends", str(len(posts)))
        shot(p, f"l5-hint-{eng}-{scheme}-{lang}.png")
        # #new sits in the right pane; the list remains; hint under the task field
        p.locator("#new-btn").click()
        p.wait_for_timeout(400)
        nb = p.locator("#new-screen").bounding_box()
        lb = p.locator("#list-screen").bounding_box()
        check(view(p) == "new" and nb and nb["x"] >= 380 and lb and lb["width"] < 420 and p.locator("#list .card").count() > 0,
              tag + " #new is the right pane", f"new {nb} list {lb}")
        check(p.locator("#list-screen").is_visible(), tag + " list stays while #new is open")
        nh = hint_text(p, "#n-hint")
        check(want in nh and send_word in nh.lower(), tag + " hint under the new-session field", nh)
        shot(p, f"l5-new-form-{eng}-{scheme}-{lang}.png")
        news = []
        p.on("request", lambda r: news.append(r.url) if r.method == "POST" and r.url.endswith("/api/new") else None)
        p.locator("#n-text").fill("one")
        p.locator("#n-text").press("Enter")
        p.wait_for_timeout(300)
        check(not news and view(p) == "new", tag + " Enter in the task field does not launch")
        p.locator("#n-text").press("Control+Enter")
        p.wait_for_timeout(600)
        check(len(news) == 1, tag + " Ctrl+Enter launches", str(len(news)))
        if view(p) != "new":
            p.locator("#new-btn").click()
            p.wait_for_timeout(300)
        p.locator("#n-text").fill("two")
        p.locator("#n-text").press("Meta+Enter")
        p.wait_for_timeout(600)
        check(len(news) == 2, tag + " ⌘+Enter launches", str(len(news)))
        shot(p, f"l5-new-{eng}-{scheme}-{lang}.png")
        check(not errs, tag + " no page errors", "; ".join(errs[:3]))
    finally:
        b.close()


def run_narrow_new(pw, eng, w, h, scheme, lang):
    tag = f"narrow-new {eng} {w}x{h} {scheme} {lang}"
    b, p, errs = new_page(pw, eng, w, h, scheme, lang)
    try:
        wait_app(p)
        check(view(p) == "list", tag + " list stays the screen", view(p))
        check(hint_text(p, "#s-hint") == "", tag + " no send hint on a narrow list")
        p.locator("#new-btn").click()
        p.wait_for_timeout(500)
        nb = p.locator("#new-screen").bounding_box()
        check(view(p) == "new" and nb and nb["x"] <= 8 and nb["width"] >= w - 16, tag + " #new still covers the phone", str(nb))
        check(hint_text(p, "#n-hint") == "", tag + " no hint under the field on a phone")
        # both shortcuts still launch; the caption is the only wide-only part
        news = []
        p.on("request", lambda r: news.append(1) if r.method == "POST" and r.url.endswith("/api/new") else None)
        p.locator("#n-text").fill("phone")
        p.locator("#n-text").press("Control+Enter")
        p.wait_for_timeout(600)
        check(len(news) == 1, tag + " Ctrl+Enter launches on a phone", str(len(news)))
        check(not errs, tag + " no page errors", "; ".join(errs[:3]))
        shot(p, f"l5-new-narrow-{eng}-{w}x{h}-{scheme}.png")
    finally:
        b.close()


def run_address(pw, eng, w, h, scheme, lang):
    """Н4: #s follows the open session; one Back returns to the list."""
    tag = f"addr {eng} {w}x{h} {scheme} {lang}"
    b, p, errs = new_page(pw, eng, w, h, scheme, lang)
    try:
        p.goto(URL + f"/app/#s={SID_A}", wait_until="load")
        p.wait_for_selector("#s-name", timeout=8000)
        p.wait_for_timeout(600)
        check(hash_sid(p) == SID_A and view(p) == "session", tag + " link opens that session", hash_sid(p))
        # «Next» after an answer
        p.wait_for_selector(".lab-ask-opt", timeout=8000)
        p.locator('.lab-ask-opt[data-k="0"]').click()
        p.wait_for_selector("#s-next:not([hidden])", timeout=8000)
        n0 = p.evaluate("history.length")
        p.locator("#s-next-go").click()
        p.wait_for_timeout(800)
        check(hash_sid(p) == SID_B and "release notes" in p.locator("#s-name").inner_text().lower(),
              tag + " address follows Next", f"{hash_sid(p)} {p.locator('#s-name').inner_text()}")
        check(p.evaluate("history.length") == n0, tag + " Next adds no history entry",
              str(p.evaluate("history.length") - n0))
        p.reload(wait_until="load")
        p.wait_for_timeout(700)
        check("release notes" in p.locator("#s-name").inner_text().lower() and hash_sid(p) == SID_B,
              tag + " reload opens the session Next showed")
        # one Back to the list
        p.locator("#session-screen [data-back]").first.click()
        p.wait_for_function("() => document.documentElement.dataset.view === 'list'", timeout=5000)
        check(view(p) == "list" and p.locator("#session-screen").is_hidden(), tag + " one Back returns to the list")
        # link → back → other card. goto the same hash the back entry may still hold: reload so boot runs.
        p.goto(URL + f"/app/#s={SID_A}", wait_until="load")
        p.wait_for_timeout(400)
        if view(p) != "session":
            p.reload(wait_until="load")
        p.wait_for_function("() => document.documentElement.dataset.view === 'session'", timeout=8000)
        p.wait_for_timeout(300)
        p.locator("#session-screen [data-back]").first.click()
        p.wait_for_function("() => document.documentElement.dataset.view === 'list'", timeout=5000)
        # history.go from the in-app back is async: measure length only after the list entry (no #s) is current
        p.wait_for_function("() => !/(?:^|#|&)s=/.test(location.hash)", timeout=5000)
        p.locator(f'#list .card[data-id="{SID_B}"]').click()
        p.wait_for_timeout(700)
        check(hash_sid(p) == SID_B, tag + " address follows the card after Back", hash_sid(p))
        d = p.evaluate("(history.state || {}).d")  # length can't show it: the push drops the forward entry Back left
        check(d == 1, tag + " that card sits one entry above the list", str(d))
        p.reload(wait_until="load")
        p.wait_for_timeout(600)
        check(hash_sid(p) == SID_B and view(p) == "session", tag + " reload opens the card that was open")
        p.locator("#session-screen [data-back]").first.click()
        p.wait_for_function("() => document.documentElement.dataset.view === 'list'", timeout=5000)
        check(view(p) == "list", tag + " Back from that card returns to the list once")
        check(not errs, tag + " no page errors", "; ".join(errs[:3]))
        shot(p, f"l5-addr-{eng}-{w}x{h}-{scheme}.png")
    finally:
        b.close()


def card_top(p):
    return p.evaluate("""() => { const c = document.querySelector('#list .card');
      return c ? Math.round(c.getBoundingClientRect().top) : -1; }""")


def run_offline(pw, eng, w, h, scheme, lang):
    tag = f"off {eng} {w}x{h} {scheme} {lang}"
    b, p, errs = new_page(pw, eng, w, h, scheme, lang)
    ctx = p.context
    try:
        wait_app(p)
        p.wait_for_timeout(300)
        y0 = card_top(p)
        check(y0 > 0, tag + " card has a place", str(y0))
        ctx.add_cookies([{"name": "fail_sessions", "value": "1", "url": URL}])
        with p.expect_response(lambda r: r.url.endswith("/api/sessions") and r.status == 500, timeout=8000):
            p.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        p.wait_for_timeout(400)
        check(p.locator(".list-off").count() == 0, tag + " first error does not show the line")
        p.wait_for_timeout(2800)
        with p.expect_response(lambda r: r.url.endswith("/api/sessions") and r.status == 500, timeout=8000):
            p.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        p.wait_for_timeout(300)
        off = p.locator(".list-off")
        text = off.inner_text() if off.count() else ""
        word = "Нет связи" if lang == "ru" else "Offline"
        check(off.count() == 1 and text.startswith(word), tag + " offline line shows", text)
        pos = off.evaluate("e => getComputedStyle(e).position")
        check(pos in ("absolute", "fixed"), tag + " line is out of flow", pos)
        y1 = card_top(p)
        check(abs(y1 - y0) <= 2, tag + " cards do not move when the line appears", f"{y0} -> {y1}")
        shot(p, f"l5-off-{eng}-{w}x{h}-{scheme}.png")
        ctx.clear_cookies()
        with p.expect_response(lambda r: r.url.endswith("/api/sessions") and r.status == 200, timeout=8000):
            p.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        p.wait_for_timeout(400)
        check(p.locator(".list-off").count() == 0, tag + " line leaves after the network returns")
        y2 = card_top(p)
        check(abs(y2 - y0) <= 2, tag + " cards stay when the line leaves", f"{y0} -> {y2}")
        # the one-toast rule (Fable Н2) stays: the second failure does not toast again
        # (toast from the first failure has already faded during the wait above)
        toast_on = p.locator("#toast").evaluate("e => !e.hidden && !e.classList.contains('off') && +getComputedStyle(e).opacity > 0.5")
        check(not toast_on, tag + " no second toast")
        check(not errs, tag + " no page errors", "; ".join(errs[:3]))
    finally:
        b.close()


def run_empty(pw, data_path, raw):
    d = no_wait(json.loads(raw))
    data_path.write_text(json.dumps(d))
    b, p, errs = new_page(pw, "chromium", 1440, 900, "light", "ru")
    try:
        p.goto(URL + "/app/", wait_until="load")
        p.wait_for_selector("#list .card, .pane-empty", timeout=8000)
        p.wait_for_timeout(500)
        shown = p.locator(".pane-empty").evaluate("e => getComputedStyle(e).display !== 'none' && e.innerText")
        check(view(p) == "list" and "Выберите сессию слева" in (shown or ""), "empty pane keeps the placeholder when nobody waits", str(shown))
        check(p.locator("#session-screen").is_hidden(), "empty pane does not open a session")
        shot(p, "l5-pane-empty.png")
        check(not errs, "empty pane no page errors", "; ".join(errs[:3]))
    finally:
        b.close()
        data_path.write_text(raw)


def run_launch(pw, eng, w, h):
    """After «Launch» the address holds #s=<id> and one Back returns to the list inside the app."""
    tag = f"launch {eng} {w}x{h}"
    b, p, errs = new_page(pw, eng, w, h, "light", "en")
    try:
        wait_app(p)
        p.wait_for_timeout(500)
        p.locator("#new-btn").click()
        p.wait_for_timeout(400)
        p.locator("#n-text").fill("launched from check")
        p.locator("#n-text").press("Control+Enter")
        p.wait_for_function("() => /(?:^|#|&)s=n/.test(location.hash)", timeout=6000)
        p.wait_for_timeout(600)
        sid = hash_sid(p)
        check(bool(sid) and sid.startswith("n") and view(p) == "session", tag + " address holds #s=<new id>", f"{sid} {view(p)}")
        p.go_back()
        p.wait_for_timeout(700)
        check(p.url.startswith(URL + "/app/") and view(p) == "list" and not hash_sid(p),
              tag + " one Back returns to the list inside the app", f"{p.url} {view(p)}")
        check(not errs, tag + " no page errors", "; ".join(errs[:3]))
    finally:
        b.close()


def run_defects(pw, eng):
    """Search Escape, chips, waiting auto-open guards, list menu focus, narrowing, poll rate."""
    tag = f"defects {eng}"
    b, p, errs = new_page(pw, eng, 1440, 900, "light", "en")
    try:
        wait_app(p)
        p.wait_for_function("() => document.documentElement.dataset.view === 'session'", timeout=8000)
        p.wait_for_timeout(500)
        # 1: Escape in the search field closes only the search (with a question open, then without)
        for sid in (SID_A, "c3e5a7b9"):
            if sid != SID_A:
                p.locator(f'#list .card[data-id="{sid}"]').click()
                p.wait_for_timeout(700)
            h0 = hash_sid(p)
            p.locator("#q-btn").click()
            p.locator("#q-field").focus()
            p.locator("#q-field").press("Escape")
            p.wait_for_timeout(500)
            check(view(p) == "session" and hash_sid(p) == h0 and p.locator("#q-row").is_hidden(),
                  tag + f" Escape in search keeps session {sid}", f"{view(p)} {hash_sid(p)}")
        # 4: chips lose the highlight while a query is set
        p.locator("#q-btn").click()
        p.locator("#q-field").fill("a")
        pressed = p.evaluate("() => [...document.querySelectorAll('#chips .chip')].map(c => c.getAttribute('aria-pressed'))")
        check(pressed and all(x == "false" for x in pressed), tag + " no pressed chip while searching", str(pressed))
        p.locator("#q-field").fill("")
        pressed = p.evaluate("() => [...document.querySelectorAll('#chips .chip')].map(c => c.getAttribute('aria-pressed'))")
        check("true" in pressed, tag + " chip highlight returns after clearing", str(pressed))
        p.locator("#q-field").press("Escape")
        # 10: wide poll reloads the list every 3rd tick even in the session view (tick 3 is ~9 s after load)
        hits = []
        p.on("request", lambda q: hits.append(1) if q.url.endswith("/api/sessions") else None)
        p.wait_for_timeout(10500)
        check(len(hits) >= 1, tag + " wide poll reloads the list in the session view", str(len(hits)))
        check(not errs, tag + " no page errors", "; ".join(errs[:3]))
    finally:
        b.close()
    # 9: narrowing keeps one history entry under the auto-opened session
    b, p, errs = new_page(pw, eng, 1440, 900, "light", "en")
    try:
        wait_app(p)
        p.wait_for_function("() => document.documentElement.dataset.view === 'session'", timeout=8000)
        p.wait_for_timeout(400)
        p.set_viewport_size({"width": 390, "height": 844})
        p.wait_for_timeout(600)
        d = p.evaluate("() => [document.documentElement.dataset.view, (history.state || {}).d]")
        check(d == ["session", 1], tag + " narrowing gives the auto-opened session a history entry", str(d))
    finally:
        b.close()
    # 8: a waiting session that arrives while a query is typed does not open in the pane
    b, p, errs = new_page(pw, eng, 1440, 900, "light", "en")
    try:
        gate = {"wait": False}
        def serve(route):
            r = route.fetch(); d = r.json()
            if not gate["wait"]:
                for x in d["sessions"]:
                    if x.get("group") == "wait" or x.get("perm"):
                        x["group"], x["state"], x["perm"] = "done", "done", None
            route.fulfill(response=r, json=d)
        p.route("**/api/sessions", serve)
        wait_app(p)
        p.wait_for_timeout(500)
        p.locator("#q-btn").click()
        p.locator("#q-field").fill("zz")
        gate["wait"] = True
        p.wait_for_timeout(10500)
        check(view(p) == "list" and not hash_sid(p), tag + " no auto-open while a query is typed", view(p))
    finally:
        b.close()
    # 9 needs the boot-opened session at depth 0; 7: list menu returns focus to «⋯»
    b, p, errs = new_page(pw, eng, 390, 844, "light", "en")
    try:
        wait_app(p)
        p.locator("#list-more").focus()
        p.keyboard.press("Enter")
        p.wait_for_timeout(300)
        p.keyboard.press("Enter")
        p.wait_for_timeout(600)
        p.keyboard.press("Escape")
        p.wait_for_timeout(600)
        a = p.evaluate("() => document.activeElement && document.activeElement.id")
        check(a == "list-more", tag + " focus returns to ⋯ after the list menu sheet", str(a))
    finally:
        b.close()


def main():
    data = Path(tempfile.gettempdir()) / f"l5-{PORT}.json"
    raw = json.dumps(boot_data())
    data.write_text(raw)
    srv = start(data)
    try:
        cases = [
            (run_wide, ("chromium", "light", "ru")),
            (run_wide, ("chromium", "dark", "en")),
            (run_wide, ("webkit", "light", "ru")),
            (run_wide, ("webkit", "dark", "ru", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15")),
            (run_narrow_new, ("chromium", 390, 844, "light", "ru")),
            (run_narrow_new, ("webkit", 393, 659, "dark", "en")),
            (run_address, ("chromium", 390, 844, "light", "ru")),
            (run_address, ("webkit", 393, 659, "dark", "en")),
            (run_launch, ("chromium", 390, 844)),
            (run_launch, ("chromium", 1440, 900)),
            (run_launch, ("webkit", 393, 659)),
            (run_launch, ("webkit", 1440, 900)),
            (run_defects, ("chromium",)),
            (run_defects, ("webkit",)),
            (run_offline, ("chromium", 390, 844, "light", "ru")),
            (run_offline, ("webkit", 393, 659, "dark", "en")),
        ]
        with sync_playwright() as pw:
            for fn, args in cases:
                if fn in (run_address, run_launch, run_defects):  # «Next» needs a fresh unanswered ask: the stand keeps answers in memory
                    srv.terminate(); srv.wait(timeout=5); data.write_text(raw); srv = start(data)
                try:
                    fn(pw, *args)
                except Exception as e:
                    check(False, fn.__name__ + " " + " ".join(map(str, args[:4])), f"{type(e).__name__}: {e}")
            try:
                run_empty(pw, data, raw)
            except Exception as e:
                check(False, "run_empty", f"{type(e).__name__}: {e}")
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()
        data.unlink(missing_ok=True)
    print("TOTAL", "PASS" if not fails else "FAIL", f"({fails} failed)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
