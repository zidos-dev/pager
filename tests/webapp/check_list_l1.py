"""Список сессий, полоса l1: две строки у «Нужно разрешение» и «Ждут вас», тихая строка квот,
когда кто-то ждёт, «Нет связи» после второй ошибки подряд, пустой список с готовыми и «Новая сессия»,
заголовок «Ждут вас» в фильтре «Ждут», значок приложения setAppBadge/clearAppBadge.

Run: PORT=22621 python3 tests/webapp/check_list_l1.py
Chromium + WebKit, 390×844 и 393×659, светлая и тёмная. SHOTS=1 кладёт кадры в /var/tmp/zp-pwa-run/l1/.
"""
import json, os, re, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "22621"))
URL = f"http://127.0.0.1:{PORT}"
SHOTS = Path("/var/tmp/zp-pwa-run/l1")
LONG_NAME = "Раскрой: обновление 2.4, общая шапка для всех проектов линейки и запас на вторую строку"
LONG_Q = "Жду ответа: какой вариант шапки оставляем на телефоне, если заголовок уже длинный и вопрос тоже не влезает в одну строку"
LONG_WORK = "Длинное название рабочей сессии которое остаётся в одну строку в группе в работе и режется"
LONG_DETAIL = "Длинная строка статуса работы которая остаётся в одну строку как раньше и обрезается многоточием в конце"

# число полностью видимых ждущих карточек на первом экране на базе 8a93c17 (замер, Chromium и WebKit, обе темы)
BASE_WAIT = {(393, 659): 4, (390, 844): 6}

L = {
    "ru": {
        "perm": "Нужно разрешение", "wait": "Ждут вас", "work": "В работе",
        "empty": "Ничего не ждёт и не работает.", "new": "Новая сессия",
        "off": "Нет связи · данные на", "fail": "Не удалось загрузить сессии",
        "h5": "5 ч", "d7": "7 дн",
    },
    "en": {
        "perm": "Needs permission", "wait": "Waiting for you", "work": "Working",
        "empty": "Nothing waiting or running.", "new": "New session",
        "off": "Offline · data from", "fail": "Couldn't load sessions",
        "h5": "5 h", "d7": "7 d",
    },
}

VISIBLE = """()=>{
  const cap = document.querySelector('#lab-fcap');
  const br = cap && cap.getBoundingClientRect();
  const limit = br && br.height > 10 && br.top > 80 ? Math.min(innerHeight, br.top) : innerHeight;
  const head = /^(Нужно разрешение|Ждут вас|Needs permission|Waiting for you)/;
  let g = '', n = 0, all = 0;
  for (const c of document.querySelectorAll('#list > *')){
    if (c.classList.contains('lsec')){ g = c.textContent.trim(); continue; }
    const card = c.querySelector && c.querySelector('.card');
    if (!card) continue;
    const r = card.getBoundingClientRect();
    if (!(r.height > 20 && r.top >= -0.5 && r.bottom <= limit + 0.5)) continue;
    all++;
    if (head.test(g)) n++;
  }
  const q = document.querySelector('#lab-q');
  const qr = q && q.getBoundingClientRect();
  return {n, all, limit: Math.round(limit), qh: qr ? Math.round(qr.height) : 0};
}"""
CLAMP = """()=>{
  const row = sel => {
    const e = document.querySelector(sel); if (!e) return null;
    const nm = e.querySelector('.nm'), ds = e.querySelector('.ds');
    const box = el => el && {clamp: getComputedStyle(el).webkitLineClamp, h: Math.round(el.getBoundingClientRect().height)};
    return {lines: e.classList.contains('lines'), nm: box(nm), ds: box(ds)};
  };
  return {perm: row('#list .card.perm'), wait: row('#list .lsec + .cw .card:not(.perm), #list .card[data-id="w0000"]'),
          work: row('#list .card[data-id="worklong"]')};
}"""
LINE = """()=>{
  const root = document.querySelector('#lab-q');
  const line = root && root.querySelector('.lqline');
  const shown = !!(line && !line.hidden && line.getBoundingClientRect().height > 2);
  const text = line ? [...line.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('') : '';
  const num = scope => !scope ? '' : [...scope.querySelectorAll('.lqd')].map(d => {
    if (d.classList.contains('z')) return '';
    const r = d.getBoundingClientRect();
    return [...d.querySelectorAll('b')].find(b => Math.abs(b.getBoundingClientRect().top - r.top) < 2)?.textContent || '';
  }).join('');
  const nums = line ? [...line.querySelectorAll('.lqx')].map(num) : [];
  const tiles = [...document.querySelectorAll('#lab-q .lqt')].filter(e => e.getBoundingClientRect().height > 2).length;
  return {quiet: !!(root && root.classList.contains('quiet')), shown, text, nums, tiles,
          h: root ? Math.round(root.getBoundingClientRect().height) : 0};
}"""


def base():
    return json.loads((ROOT.parent / "stand" / "demo2.json").read_text())


def crowd():
    d = base()
    rows = d["sessions"]["sessions"]
    proto = dict(next(s for s in rows if s["group"] == "wait"))
    rows[0].update(perm=True, perm_title="Bash", perm_body="git push origin " + "main " * 40,
                   name=LONG_NAME)
    rows[1].update(name="Раскрой: обновление 2.4, запасной заголовок второй очереди",
                   wait_question={"question": LONG_Q, "options": []})
    for i in range(12):
        rows.append(dict(proto, id=f"w{i:04d}", name=f"{LONG_NAME} · {i}", ago=f"{i + 3} min",
                         updated=1792015000 - i, detail="", perm=False,
                         wait_question={"question": f"{LONG_Q} · {i}", "options": []}))
    work = next(s for s in rows if s["group"] == "work")
    work.update(id="worklong", name=LONG_WORK, detail=LONG_DETAIL)
    return d


def done_only():
    d = base()
    proto = next(s for s in d["sessions"]["sessions"] if s["group"] == "done")
    rows = []
    for i, upd in enumerate((40, 30, 20, 10)):
        rows.append(dict(proto, id=f"dn{i}", name=f"Готово {i}", base_name=f"Готово {i}",
                         group="done", state="done", updated=upd, ago=f"{i + 1} h", detail="собрано", perm=False))
    d["sessions"]["sessions"] = rows
    return d


def waiting_n(d):
    return sum(1 for s in d["sessions"]["sessions"] if s.get("group") == "wait" or s.get("perm"))


FAILS = 0


def check(ok, name, detail=""):
    global FAILS
    FAILS += 0 if ok else 1
    print(("PASS" if ok else "FAIL"), name, detail, flush=True)


def shot(page, name):
    if os.environ.get("SHOTS") != "1":
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / name))


def groups(page):
    return page.evaluate("""()=>{let g='',o=[];for(const c of document.querySelectorAll('#list > *')){
      if(c.classList.contains('lsec')){g=c.textContent.trim();o.push(g);continue}}return o}""")


def run_case(pw, engine, width, height, theme, lang, offline, datafile):
    tag = f"{engine} {width}x{height} {theme} {lang}"
    browser = getattr(pw, engine).launch()
    ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2,
                              is_mobile=True, has_touch=True, locale="ru-RU" if lang == "ru" else "en-US",
                              color_scheme=theme, service_workers="block")
    ctx.add_init_script("(() => {\n"
        "  const lang = " + json.dumps(lang) + ";\n"
        "  try { localStorage.setItem('cc-lang', lang); localStorage.setItem('cc-filter', 'act'); } catch (e) {}\n"
        "  window.__badges = []; window.__rej = 0;\n"
        "  window.addEventListener('unhandledrejection', () => { window.__rej++; });\n"
        "  const install = (name, fn) => {\n"
        "    try { navigator[name] = fn; } catch (e) {}\n"
        "    if (navigator[name] === fn) return;\n"
        "    try { Object.defineProperty(navigator, name, {configurable:true, writable:true, value:fn}); } catch (e) {}\n"
        "    if (navigator[name] === fn) return;\n"
        "    try { Object.defineProperty(Navigator.prototype, name, {configurable:true, writable:true, value:fn}); }\n"
        "    catch (e) { window.__badgeStub = String(e); }\n"
        "    if (navigator[name] !== fn) window.__badgeStub = window.__badgeStub || 'not replaced';\n"
        "  };\n"
        "  install('setAppBadge', n => { window.__badges.push(['set', n]); return Promise.reject(new Error('badge')); });\n"
        "  install('clearAppBadge', () => { window.__badges.push(['clear']); return Promise.resolve(); });\n"
        "  return 0;\n"
        "})()")
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    word = L[lang]
    try:
        datafile.write_text(json.dumps(crowd()))
        page.goto(URL + "/app/", wait_until="load")
        page.wait_for_selector("#list .card")
        page.wait_for_timeout(700)
        vis = page.evaluate(VISIBLE)
        print(f"VISIBLE {tag} waiting={vis['n']} all={vis['all']} quota_h={vis['qh']} limit={vis['limit']}", flush=True)
        clamp = page.evaluate(CLAMP)
        base_n = BASE_WAIT[(width, height)]
        check(vis["n"] > base_n, f"{tag}: fully visible waiting cards {vis['n']} > base {base_n}", str(vis))
        box = clamp["perm"]
        check(box and box["lines"] and box["nm"]["clamp"] == "1" and box["ds"]["clamp"] == "2" and box["ds"]["h"] > 24,
              f"{tag}: perm name is one line, question uses 2", str(box))
        box = clamp["wait"]
        check(box and not box["lines"] and box["nm"]["clamp"] == "1" and box["ds"]["clamp"] == "1",
              f"{tag}: crowded wait row is one line + one line (density)", str(box))
        work = clamp["work"]
        check(work and not work["lines"] and work["nm"]["clamp"] == "1" and work["ds"]["clamp"] == "1",
              f"{tag}: work row stays one line", str(work))
        datafile.write_text(json.dumps(base()))
        page.reload(wait_until="load")
        page.wait_for_selector("#list .card")
        page.wait_for_timeout(500)
        few = page.evaluate(CLAMP)["wait"]
        check(waiting_n(base()) > 3 or (few and few["lines"] and few["nm"]["clamp"] == "1" and few["ds"]["clamp"] == "2"),
              f"{tag}: few waiting: name 1 line, question up to 2", str(few))
        datafile.write_text(json.dumps(crowd()))
        page.reload(wait_until="load")
        page.wait_for_selector("#list .card")
        page.wait_for_timeout(500)
        line = page.evaluate(LINE)
        check(line["quiet"] and line["shown"] and line["tiles"] == 0, f"{tag}: quota tiles collapse to one line", str(line))
        check(word["h5"] in line["text"] and word["d7"] in line["text"] and "Claude" in line["text"],
              f"{tag}: quiet line names both quotas", str(line))
        check(line["nums"] == ["34", "18"], f"{tag}: quiet line shows 34% and 18%", str(line["nums"]))
        check(44 <= line["h"] <= 64, f"{tag}: quiet quota line is one short row", str(line["h"]))
        page.locator("#lab-q").click()
        page.wait_for_timeout(700)
        check(page.locator("#quota-sheet").is_visible(), f"{tag}: tapping the quiet line opens the quota sheet")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        gs = groups(page)
        check(any(g.startswith(word["wait"]) for g in gs), f"{tag}: Active keeps the waiting header", str(gs))
        page.locator('.chip[data-f="wait"]').evaluate("e=>e.click()")
        page.wait_for_timeout(200)
        gw = groups(page)
        check(gw and gw[0].startswith(word["perm"]) and any(g.startswith(word["wait"]) for g in gw[1:]),
              f"{tag}: Waiting filter has a Waiting-for-you header after permissions", str(gw))
        shot(page, f"wait-filter-{engine}-{width}x{height}-{theme}-{lang}.png")
        page.locator('.chip[data-f="act"]').evaluate("e=>e.click()")
        page.wait_for_timeout(150)
        shot(page, f"list-quiet-{engine}-{width}x{height}-{theme}-{lang}.png")
        badges = page.evaluate("()=>({b:window.__badges||null, rej:window.__rej||0, stub:window.__badgeStub||'', typ:typeof navigator.setAppBadge})")
        n = waiting_n(json.loads(datafile.read_text()))
        sets = [x[1] for x in (badges["b"] or []) if x[0] == "set"]
        check(sets and all(x == n for x in sets) and badges["rej"] == 0 and not badges["stub"],
              f"{tag}: setAppBadge({n}) and a rejected badge is swallowed", str(badges))
        check(not errs, f"{tag}: no page errors", str(errs[:3]))

        datafile.write_text(json.dumps(done_only()))
        page.goto(URL + "/app/", wait_until="load")
        page.wait_for_selector("#list .empty, #list .card")
        page.wait_for_timeout(600)
        body = page.locator("#list").inner_text()
        check(word["empty"] in body, f"{tag}: empty line stays", body[:180])
        ids = page.evaluate("()=>[...document.querySelectorAll('#list .card')].map(c=>c.dataset.id)")
        check(ids == ["dn0", "dn1", "dn2"], f"{tag}: three newest done sessions", str(ids))
        btn = page.locator("#list [data-new]")
        check(btn.count() == 1 and btn.inner_text().strip() == word["new"], f"{tag}: New session button", btn.inner_text() if btn.count() else "")
        box = btn.bounding_box() if btn.count() else None
        check(box and box["height"] >= 44, f"{tag}: New session target ≥44", str(box))
        quiet = page.evaluate(LINE)
        check(not quiet["quiet"] and quiet["tiles"] >= 2, f"{tag}: tiles stay when nobody is waiting", str(quiet))
        btn.click()
        page.wait_for_timeout(600)
        check(page.evaluate("document.documentElement.dataset.view") == "new", f"{tag}: New session opens the same screen as +")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        page.locator("#new-btn").click()
        page.wait_for_timeout(500)
        check(page.evaluate("document.documentElement.dataset.view") == "new", f"{tag}: + still opens the new-session screen")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        shot(page, f"list-empty-{engine}-{width}x{height}-{theme}-{lang}.png")
        cleared = page.evaluate("()=>window.__badges")
        check(cleared and all(x[0] == "clear" for x in cleared) and not any(x[0] == "set" for x in cleared),
              f"{tag}: clearAppBadge when nothing is waiting", str(cleared))

        if offline:
            datafile.write_text(json.dumps(crowd()))
            page.goto(URL + "/app/", wait_until="load")
            page.wait_for_selector("#list .card")
            page.wait_for_timeout(400)
            check(page.locator(".list-off").count() == 0, f"{tag}: no offline line on fresh data")
            ctx.add_cookies([{"name": "fail_sessions", "value": "1", "url": URL}])
            with page.expect_response(lambda r: r.url.endswith("/api/sessions") and r.status == 500, timeout=8000):
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(300)
            check(page.locator(".list-off").count() == 0, f"{tag}: first refresh error does not mark the list offline")
            toast = page.locator("#toast")
            check(word["fail"] in (toast.inner_text() or ""), f"{tag}: failure toast stays", toast.inner_text() if toast.count() else "")
            page.wait_for_timeout(3200)  # тост гаснет сам, чтобы второй был отдельным
            with page.expect_response(lambda r: r.url.endswith("/api/sessions") and r.status == 500, timeout=8000):
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(300)
            off = page.locator(".list-off")
            text = off.inner_text() if off.count() else ""
            check(off.count() == 1 and text.startswith(word["off"]) and re.search(r"\d{2}:\d{2}", text),
                  f"{tag}: second error shows the offline line", text)
            check(page.locator("#list > :first-child").evaluate("e=>e.classList.contains('list-off')"), f"{tag}: offline line is the first row")
            tv = page.locator("#toast").evaluate("e=>!e.hidden&&!e.classList.contains('off')&&+getComputedStyle(e).opacity>0.5")
            check(not tv, f"{tag}: second failure does not toast again (the offline line speaks; Fable Н2)")
            shot(page, f"list-offline-{engine}-{width}x{height}-{theme}-{lang}.png")
            ctx.clear_cookies()
            with page.expect_response(lambda r: r.url.endswith("/api/sessions") and r.status == 200, timeout=8000):
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(400)
            check(page.locator(".list-off").count() == 0, f"{tag}: offline line leaves after the first success")
            check(not errs, f"{tag}: no page errors after offline", str(errs[:3]))
    finally:
        ctx.close()
        browser.close()


def main():
    data = Path(tempfile.gettempdir()) / f"list-l1-{PORT}.json"
    data.write_text(json.dumps(base()))
    server = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")],
                              env=dict(os.environ, DATA_FILE=str(data), PORT=str(PORT)),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(URL + "/api/sessions", timeout=.3).close()
                break
            except OSError:
                time.sleep(.1)
        else:
            print("FAIL stand did not start")
            return 1
        cases = [
            ("chromium", 390, 844, "light", "ru", False),
            ("chromium", 393, 659, "light", "ru", True),
            ("chromium", 393, 659, "light", "en", True),
            ("chromium", 393, 659, "dark", "ru", False),
            ("webkit", 390, 844, "light", "ru", True),
            ("webkit", 393, 659, "light", "en", False),
            ("webkit", 390, 844, "dark", "ru", False),
        ]
        with sync_playwright() as pw:
            for engine, w, h, theme, lang, offline in cases:
                try:
                    run_case(pw, engine, w, h, theme, lang, offline, data)
                except Exception as e:
                    global FAILS
                    FAILS += 1
                    print(f"FAIL {engine} {w}x{h} {theme} {lang}: {type(e).__name__}: {e}", flush=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        data.unlink(missing_ok=True)
    print("TOTAL", "PASS" if not FAILS else "FAIL", f"({FAILS} failed)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
