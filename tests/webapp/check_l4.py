"""List header (lane l4): «⋯» menu (№11), search field (F3), press tone of terminal keys / quote / pill (Н5, Н6).

Usage: PORT=19341 python3 check_l4.py   (own stand on a temp copy of tests/stand/demo2.json; chromium + webkit)
Env: BASE_WAIT=<n> — waiting cards visible at 393x659 on the code before the search field (density floor).
"""
import json, os, subprocess, sys, tempfile, time, urllib.request
from io import BytesIO
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "19341")); URL = f"http://127.0.0.1:{PORT}"
APP = Path(os.environ.get("APP_DIR", ROOT.parent.parent / "assets/webapp"))
BASE_WAIT = int(os.environ.get("BASE_WAIT", "0"))
SID_Q, SID_TERM = "a1c3e5f7", "h2y8b1ef"
LONG = ("Before I touch anything: the session cache currently lives in process memory, so every restart drops all logins. "
        "There are three places it can go and each has a different failure mode. Redis is shared between instances and survives restarts, "
        "but adds a network hop to every request. An in-memory LRU is the simplest, yet each instance has its own copy. "
        "A Postgres table is durable, but it makes the hot path slower. One decision is yours: where should it live?")
ONLY = set(os.environ.get("ONLY", "").split(",")) - {""}
run = lambda k: not ONLY or k in ONLY
bad = 0
T = {"en": dict(more="More", rows=["Bots", "Permissions", "Model", "Quotas"], none="Nothing found"),
     "ru": dict(more="Ещё", rows=["Боты", "Права", "Модель", "Квоты"], none="Ничего не найдено")}


def check(ok, name, detail=""):
    global bad; bad += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)


def lin(c): c /= 255; return c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4
def lstar(c):
    y = .2126 * lin(c[0]) + .7152 * lin(c[1]) + .0722 * lin(c[2]); return 116 * (y ** (1 / 3)) - 16 if y > .008856 else 903.3 * y
def patch(page, x, y):
    im = Image.open(BytesIO(page.screenshot(clip={"x": x, "y": y, "width": 4, "height": 4}))).convert("RGB")
    px = list(im.getdata()); return [sum(p[i] for p in px) / len(px) for i in range(3)]


def mkdata():
    d = json.loads((ROOT.parent / "stand/demo2.json").read_text())
    d["session"][SID_Q]["history"][-1] = {"role": "assistant", "uuid": "last-long", "ts": "2026-10-14T09:59:00.000Z", "html": LONG, "files": []}
    for s in d["sessions"]["sessions"]:
        if s["id"] == "e5a7c9d1": s["cwd"] = "/srv/zebrafolder"
    f = Path(tempfile.mkdtemp()) / "d.json"; f.write_text(json.dumps(d)); return f


def new_page(pw, eng, w, h, scheme="light", lang="en", url="/app/"):
    b = getattr(pw, eng).launch()
    c = b.new_context(viewport={"width": w, "height": h}, color_scheme=scheme)
    c.route("https://telegram.org/**", lambda r: r.fulfill(body=""))
    c.add_init_script(f"try{{localStorage.setItem('cc-lang','{lang}')}}catch(e){{}}")
    p = c.new_page(); p.set_default_timeout(4000); p.goto(URL + url); p.wait_for_selector("#list .card"); p.wait_for_timeout(600); return b, p


def cards(p): return p.evaluate("()=>[...document.querySelectorAll('#list .card')].map(c=>c.querySelector('.n,b,strong')?c.innerText.split('\\n')[0]:c.innerText)")


def press(page, x, y, cx, cy, name, need):
    rest = patch(page, x, y)
    page.mouse.move(cx, cy); page.mouse.down(); page.wait_for_timeout(260)
    down = patch(page, x, y)
    page.mouse.move(2, 2); page.mouse.up(); page.wait_for_timeout(150)
    d = abs(lstar(down) - lstar(rest)); check(d >= need, f"press tone {name}", f"dL*={d:.1f} need>={need}")


srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, DATA_FILE=str(mkdata()), PORT=str(PORT), APP_DIR=str(APP)),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen(URL + "/api/sessions", timeout=.3).close(); break
        except OSError: time.sleep(.1)
    else: sys.exit(f"stand did not start on {PORT}")
    with sync_playwright() as pw:
        for eng in ("chromium", "webkit"):
            # ---- №11: one «⋯» menu instead of three bare icons
            for lang in (("en", "ru") if run("menu") else ()):
                b, p = new_page(pw, eng, 390, 844, "light", lang); tag = f"№11 {eng} {lang}"
                s = T[lang]
                for old in ("bots-btn", "perms-btn", "model-btn"):
                    check(not p.locator("#" + old).is_visible(), f"{tag} #{old} is no longer a header icon")
                more = p.locator("#list-more")
                check(more.count() == 1 and more.is_visible() and (more.get_attribute("aria-label") or "") != "", f"{tag} «⋯» button with aria-label", str(more.get_attribute("aria-label")))
                bb = more.bounding_box(); check(bb and bb["width"] >= 44 and bb["height"] >= 44, f"{tag} «⋯» target >=44", str(bb))
                check(p.locator("#new-btn").is_visible(), f"{tag} «+» stays")
                for key, sheet, label in (("bots", "#bots-sheet", s["rows"][0]), ("perms", "#perms-sheet", s["rows"][1]), ("model", "#model-sheet", s["rows"][2]), ("quota", "#quota-sheet", s["rows"][3])):
                    more.click(); p.wait_for_timeout(600)
                    row = p.locator(f'.mn-row[data-k="{key}"]')
                    check(row.count() == 1 and label.lower() in row.inner_text().lower(), f"{tag} row {key} labelled", row.inner_text() if row.count() else "none")
                    if not row.count(): break
                    inb = p.evaluate("()=>{const m=document.querySelector('.mn-menu').getBoundingClientRect();return m.left>=0&&m.right<=innerWidth&&m.top>=0&&m.bottom<=innerHeight}")
                    check(inb, f"{tag} menu inside the window")
                    row.click(); p.wait_for_timeout(900)
                    check(p.locator(sheet).is_visible(), f"{tag} row {key} opens {sheet}")
                    p.keyboard.press("Escape"); p.wait_for_timeout(500)
                    if p.locator(sheet).is_visible(): p.evaluate("()=>document.querySelector('.sheet:not([hidden]) [data-close-sheet]')?.click()"); p.wait_for_timeout(500)
                more.click(); p.wait_for_timeout(500); p.keyboard.press("Escape"); p.wait_for_timeout(500)
                check(p.locator(".mn-menu").count() == 0 or not p.locator(".mn-menu").is_visible(), f"{tag} Esc closes the menu")
                b.close()
            # ---- F3: search
            for (w, h) in (((390, 844), (393, 659)) if run("search") else ()):
                b, p = new_page(pw, eng, w, h, "light", "ru"); tag = f"F3 {eng} {w}x{h}"
                def vis_wait():
                    return p.evaluate("()=>{const vh=innerHeight;return [...document.querySelectorAll('#list .card')].filter(c=>{const r=c.getBoundingClientRect();return r.top>=0&&r.bottom<=vh}).length}")
                closed = vis_wait(); print("   visible cards, search closed:", closed)
                if BASE_WAIT and h == 659: check(closed >= BASE_WAIT, f"{tag} density not below base", f"{closed} vs {BASE_WAIT}")
                btn = p.locator("#q-btn"); check(btn.count() == 1 and btn.is_visible(), f"{tag} search button in the header")
                if btn.count() != 1: b.close(); continue
                check(p.locator("#q-field").is_hidden(), f"{tag} field closed by default (list density kept)")
                btn.click(); p.wait_for_timeout(500)
                f = p.locator("#q-field"); check(f.is_visible(), f"{tag} field opens"); f.fill("MIGRATE"); p.wait_for_timeout(250)
                names = cards(p); check(len(names) == 1 and "Migrate" in names[0], f"{tag} case-insensitive name filter, all groups", str(names))
                f.fill("zebrafolder"); p.wait_for_timeout(250)
                names = cards(p); check(len(names) == 1 and "flaky" in names[0].lower(), f"{tag} folder filter", str(names))
                f.fill("qqqzzz"); p.wait_for_timeout(250)
                check(p.locator("#list .card").count() == 0 and "Ничего не найдено" in p.locator("#list").inner_text(), f"{tag} empty result line")
                f.fill(""); p.wait_for_timeout(250)
                check(p.locator("#list .card").count() >= closed and "Ничего не найдено" not in p.locator("#list").inner_text(), f"{tag} clearing restores the list")
                b.close()
            # ---- Н5, Н6: press tone
            for scheme in (("light", "dark") if run("press") else ()):
                b, p = new_page(pw, eng, 390, 844, scheme, "en", f"/app/#s={SID_TERM}"); p.wait_for_timeout(1400); tag = f"{eng} {scheme}"
                k = p.locator('#s-keys [data-k="y"]').bounding_box()
                if k: press(p, k["x"] + 10, k["y"] + k["height"] / 2 - 2, k["x"] + k["width"] / 2, k["y"] + k["height"] / 2, f"terminal key y {tag}", 15 if scheme == "light" else 12)
                else: check(False, f"{tag} terminal keys visible")
                b.close()
                b, p = new_page(pw, eng, 390, 844, scheme, "en", f"/app/#s={SID_Q}"); p.wait_for_timeout(1500)
                q = p.locator(".lab-ask-quote").first.bounding_box()
                if q: press(p, q["x"] + q["width"] - 9, q["y"] + q["height"] / 2, q["x"] + q["width"] - 5, q["y"] + q["height"] / 2, f"quote {tag}", 10)
                else: check(False, f"{tag} quote visible")
                p.mouse.click(195, 40); p.wait_for_timeout(1000)
                pl = p.locator(".lab-ask-pill").bounding_box()
                if pl: press(p, pl["x"] + 6, pl["y"] + pl["height"] / 2 - 2, pl["x"] + 20, pl["y"] + pl["height"] / 2, f"pill {tag}", 10)
                else: check(False, f"{tag} pill visible")
                b.close()
finally:
    srv.kill()
print("TOTAL", "PASS (0 failed)" if not bad else f"FAIL ({bad} failed)"); sys.exit(1 if bad else 0)
