"""Permission rows in the session list; run externally with Playwright."""
import json, os, subprocess, sys, tempfile, time, urllib.request
from copy import deepcopy
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
import check_perm as cp
from check_perm import check, TMUX_ID, SDK_ID, URL

L = {"en": ("Needs permission · 2", "Waiting for you"), "ru": ("Нужно разрешение · 2", "Ждут вас")}
# Поля сервера в строках списка: у tmux только perm_title (идёт в подпись), у SDK ещё perm_body (подпись — тело, заголовок — в пилюле)
DATA = deepcopy(cp.BASE)
for _row in DATA["sessions"]["sessions"]:
    if _row["id"] == TMUX_ID: _row["perm_title"] = "Bash command"
    if _row["id"] == SDK_ID: _row.update(perm_title="Bash", perm_body="git log --oneline -5")
    if _row["id"] == "a1c3e5f7": _row["wait_question"] = {"question": "Where should the session cache live?", "options": []}


def rows_case(lang, theme, engine, shot):
    def run(page, ctx, state):
        cp.ready(page, theme)
        # ждущие — строками, группами наверху (полку убрали по решению владельца 19.10)
        check(page.locator("#list .shelf").count() == 0, "shelf is back")
        first = page.locator("#list .lsec").first.inner_text().strip()
        check(first == L[lang][0], f"first group {first!r}")
        check(page.locator("#list > :first-child").evaluate("e=>e.classList.contains('lsec')"), "group not at top")
        kids = page.locator("#list").evaluate("""e=>{const o=[];let g='';for(const c of e.children){
          if(c.classList.contains('lsec')){g=c.textContent;continue}const k=c.querySelector('.card');if(k)o.push([g,k.dataset.id])}return o}""")
        perm = [i for g, i in kids if g.startswith(L[lang][0].split(" · ")[0])]
        check(sorted(perm) == sorted([TMUX_ID, SDK_ID]), f"perm group members {perm}")
        wait = [i for g, i in kids if g.startswith(L[lang][1])]
        check(wait and TMUX_ID not in wait and SDK_ID not in wait, f"Waiting group {wait}")
        for sid in (TMUX_ID, SDK_ID):
            check(page.locator(f'#list .card[data-id="{sid}"]').count() == 1, f"perm session {sid} shown twice")
            c = page.locator(f'#list .cw .card[data-id="{sid}"]')
            check(c.evaluate("e=>e.classList.contains('perm')"), "perm card class")
            check(c.locator("svg.pi use").get_attribute("href") == "#i-lock", "no lock svg")
            check(c.locator(".dot").count() == 0, "status dot present")
            sz = c.locator("svg.pi").evaluate("e=>[e.getBoundingClientRect().width,e.getBoundingClientRect().height]")
            check(sz[0] == sz[1] and 12 <= sz[0] <= 18, f"icon size {sz}")
            ds = c.locator(".ds").inner_text()
            if sid == TMUX_ID:  # без тела команды — заголовок разрешения
                check(ds == L[lang][0].split(" · ")[0] + " · Bash command", f"caption from perm_title {ds!r}")
            else:  # вторая строка — сама команда (решение владельца 19.10)
                check(ds == "Bash · git log --oneline -5", f"caption from perm_body {ds!r}")
            check(c.locator(".go").count() == 0, "answer button in a row")
            check(int(c.locator(".nm").evaluate("e=>getComputedStyle(e).fontWeight")) >= 600, "name weight")
        q = page.locator('#list .card[data-id="a1c3e5f7"] .ds').inner_text()
        check(q == "Where should the session cache live?", f"waiting row shows the question {q!r}")
        # «Ждут» filter keeps the group on top too
        page.locator('.chip[data-f="wait"]').evaluate('e=>e.click()')
        check(page.locator("#list .lsec").first.inner_text().strip() == L[lang][0], "wait filter group")
        page.locator('.chip[data-f="act"]').evaluate('e=>e.click()')
        if shot:
            page.wait_for_timeout(300)
            page.screenshot(path=str(cp.ROOT / f"permrows-{shot}.png"))
        t0 = time.monotonic()
        page.locator(f'.card[data-id="{TMUX_ID}"]').click()
        root = page.locator('.lab-ask-root.permission[data-state="open"]')
        expect(root).to_be_visible(timeout=1500)
        check(time.monotonic() - t0 <= 1.6, "sheet slower than 1.5s")
    return run


def main():
    failures = count = 0
    with tempfile.TemporaryDirectory(prefix="cc-permrows-") as tmp:
        state = cp.State(Path(tmp) / "demo.json", Path(tmp) / "perm.jsonl", Path(tmp) / "gets.jsonl")
        state.reset()
        env = dict(os.environ, DATA_FILE=str(state.datafile), PERM_LOG=str(state.logfile), GET_LOG=str(state.getlog), PORT=str(cp.PORT))
        server = subprocess.Popen([sys.executable, str(cp.ROOT / "stand_server.py")], env=env, stdout=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    urllib.request.urlopen(URL + "/api/sessions", timeout=.3).close(); break
                except OSError:
                    time.sleep(.1)
            with sync_playwright() as pw:
                for engine in os.environ.get("BROWSERS", "chromium,webkit").split(","):
                    browser = getattr(pw, engine).launch()
                    for lang in ("en", "ru"):
                        for theme in ("light", "dark"):
                            shot = theme if engine == "chromium" and lang == "en" else None
                            state.reset(DATA)
                            ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="en-US")
                            ctx.add_init_script("localStorage.setItem('cc-lang'," + json.dumps(lang) + ")")
                            ctx.route("https://telegram.org/**", lambda r: r.fulfill(body=""))
                            page = ctx.new_page(); errors = []
                            page.on("pageerror", lambda e: errors.append(str(e)))
                            count += 1
                            try:
                                rows_case(lang, theme, engine, shot)(page, ctx, state)
                                check(not errors, f"page errors {errors}")
                                print(f"PASS {engine} {lang} {theme}", flush=True)
                            except Exception as e:
                                failures += 1; print(f"FAIL {engine} {lang} {theme}: {(str(e) if os.environ.get('VERBOSE') else str(e).splitlines()[0])}", flush=True)
                            finally:
                                ctx.close()
                    browser.close()
        finally:
            server.terminate(); server.wait(timeout=5)
    print(f"TOTAL {count - failures} PASS, {failures} FAIL", flush=True)
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
