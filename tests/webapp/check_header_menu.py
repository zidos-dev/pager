"""Header/menu/update acceptance, 390x844, Chromium + WebKit, RU/EN, light/dark.

APP_DIR selects the product checkout. RUN_REGRESSIONS=1 also runs the three
existing suites after this suite releases its server. BROWSERS can narrow local
triage; the acceptance default runs both. No screenshots/checks are pre-recorded.

Morph samples are real paused WAAPI frames at playbackRate=.1 (300/1500/3000ms
of slowed time). A 592ms source animation takes 5920ms at that rate: the TZ's
3000ms fullscreen requirement contradicts its requested slowdown. We require
monotonic growth at those samples, then fullscreen at the actual slowed end.
The visible clip rectangle is measured: DOM bbox alone is always fullscreen.
The stand rejects HEAD with 501 like the bot server and supplies cookies for version/error changes. These
checks cannot establish production server HEAD support, deployment cache rules,
or installed-device service-worker freshness (service workers are blocked here).
"""
import json
import os
from copy import deepcopy
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent
APP = Path(os.environ.get("APP_DIR", str(Path(__file__).resolve().parents[2] / "assets/webapp")))
BASE = json.loads((ROOT.parent / "stand/demo2.json").read_text())
PORT = int(os.environ.get("PORT", "18987"))
URL = f"http://127.0.0.1:{PORT}"
SID = "a1c3e5f7"
ASSETS = {"/app/" + name for name in ("index.html", "clio.js", "clio.css", "menu.js", "menu.css", "voice.js")}
LONG_NAME = "Header acceptance: a deliberately very long session title " * 3


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def fixture():
    data = deepcopy(BASE)
    s = data["session"][SID]
    s.update(name=LONG_NAME, base_name=LONG_NAME, question=None, perm=None,
             prev={"id": "b2d4f6a8", "name": "Previous session"}, k=2,
             subagents_count={"working": 1, "total": 3})
    for row in data["sessions"]["sessions"]:
        if row["id"] == SID:
            row.update(name=LONG_NAME, base_name=LONG_NAME)
    return data


def write_fixture(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data))
    temporary.replace(path)


# Record actual fetch completion, including WebKit's background fetches. Also
# retain the real 120s callback: invoking it tests the interval without sleeping
# two minutes or advancing unrelated polling/navigation timers.
INIT = """(() => {
  window.__hdrHeads=[]; window.__hdrIntervals=[];
  const fetch0=window.fetch.bind(window);
  window.fetch=async function(input, options) {
    // проверка версии — GET с cache:no-store к файлам приложения (сервер бота не умеет HEAD)
    const url=new URL(typeof input==='string'?input:input.url,location.href).pathname;
    if(!(options&&options.cache==='no-store'&&/^\/app\/[^/]+\.(html|js|css)$/.test(url))) return fetch0(input,options);
    const row={url, cache:options.cache};
    try { const r=await fetch0(input,options); row.status=r.status;
      row.length=String((await r.clone().arrayBuffer()).byteLength); return r;
    } finally { window.__hdrHeads.push(row); }
  };
  const interval0=window.setInterval.bind(window);
  window.setInterval=function(fn,ms,...args) {
    if(ms===120000) window.__hdrIntervals.push(()=>fn(...args));
    return interval0(fn,ms,...args);
  };
})()"""


def ready(page):
    host_theme = getattr(page, "_hdr_host_theme", None)
    query = "?theme=tg-" + host_theme if host_theme else ""
    page.goto(URL + "/app/" + query, wait_until="load")
    if host_theme:
        # colorScheme selects Clio's theme; the host variables independently
        # select the product palette. The stand injects Telegram's real shape.
        secondary = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--tg-theme-secondary-bg-color').trim()")
        check(secondary == ("#000" if host_theme == "dark" else "#efeff4"),
              f"Telegram {host_theme} host palette missing: {secondary!r}")
    expect(page.locator(f'#list .card[data-id="{SID}"]')).to_be_visible()
    page.wait_for_function(f"window.__hdrHeads.length>={len(ASSETS)}")
    heads = page.evaluate(f"window.__hdrHeads.slice(0,{len(ASSETS)})")
    check({r["url"] for r in heads} == ASSETS, f"baseline misses assets: {heads}")
    check(all(r["status"] == 200 and int(r["length"]) > 0 and r["cache"] == "no-store" for r in heads),
          f"baseline version GET must be successful, no-store, with length: {heads}")


def opened(page):
    ready(page)
    page.locator(f'#list .card[data-id="{SID}"]').click()
    expect(page.locator("#session-screen")).to_be_visible()
    expect(page.locator("#s-name")).to_have_text(LONG_NAME)
    page.wait_for_timeout(1000)


def shot(page, stem, engine, lang, surface, theme):
    # Keep the requested unqualified names as well as every matrix cell.
    page.screenshot(path=str(ROOT / f"{stem}-{engine}-{lang}-{surface}-{theme}.png"))
    if engine == "webkit" and lang == "en" and surface == "pwa":
        if stem in ("hdr-top", "hdr-bottom"):
            page.screenshot(path=str(ROOT / f"hdr-{theme}-{stem[4:]}.png"))
        elif stem == "hdr-menu":
            page.screenshot(path=str(ROOT / f"hdr-menu-{theme}.png"))


def header_menu(page, ctx, engine, lang, surface, theme):
    opened(page)
    header = page.locator("#session-screen > header.session-header")
    expect(header).to_be_visible()
    expect(page.locator("html")).to_have_attribute("data-lab-theme", theme)
    back = header.locator("[data-back]")
    if surface == "pwa":
        expect(back).to_be_visible()
    else:
        expect(back).to_be_hidden()
    expect(header.locator("#s-dot")).to_be_visible()
    check(page.locator("#s-body header.session-header, #s-body .head h2").count() == 0,
          "header/title remains inside scroll body")
    for ident in ("s-name", "s-state", "s-more", "s-subs", "s-chain"):
        expect(header.locator("#" + ident)).to_be_visible()
    css = page.locator("#s-name").evaluate("""e=>{const s=getComputedStyle(e);return {
      white:s.whiteSpace,overflow:s.overflow,textOverflow:s.textOverflow,
      font:parseFloat(s.fontSize),weight:+s.fontWeight,h:e.clientHeight,
      line:parseFloat(s.lineHeight),width:e.clientWidth,scroll:e.scrollWidth};}""")
    check(css["white"] == "nowrap" and css["textOverflow"] == "ellipsis" and css["overflow"] == "hidden",
          f"title is not single-line ellipsis: {css}")
    check(css["font"] == 17 and css["weight"] == 600 and css["scroll"] > css["width"], f"title typography/truncation: {css}")
    check(css["h"] <= css["line"] + 1, f"title wraps: {css}")
    check(page.locator("#s-state").inner_text().strip(), "empty status")
    check(page.locator("#s-state").evaluate("e=>parseFloat(getComputedStyle(e).fontSize)") == 13,
          "status is not 13px")
    check(header.evaluate("e=>getComputedStyle(e).borderBottomWidth") == "0px", "header bottom divider")
    more = page.locator("#s-more").bounding_box()
    check(more["width"] >= 44 and more["height"] >= 44, f"more target too small: {more}")
    pills = [page.locator("#" + ident).bounding_box() for ident in ("s-subs", "s-chain")]
    check(all(r["height"] >= 44 for r in pills), f"pill targets/heights: {pills}")
    a, b = pills  # владелец 19.10: текст пилюль не режется — вторая пилюля может уйти на строку ниже, но не накрыть первую
    check(a["x"] + a["width"] <= b["x"] + 1 or b["x"] + b["width"] <= a["x"] + 1 or a["y"] + a["height"] <= b["y"] + 1 or b["y"] + b["height"] <= a["y"] + 1,
          f"pills overlap: {pills}")
    cut = [page.locator("#" + ident).evaluate("e=>e.scrollWidth-e.clientWidth") for ident in ("s-subs", "s-chain")]
    check(all(c <= 1 for c in cut), f"pill text cut: {cut}")
    original = header.bounding_box()
    for position in ("bottom", "top"):
        page.locator("#s-body").evaluate("(e,bottom)=>e.scrollTop=bottom?e.scrollHeight:0", position == "bottom")
        page.wait_for_timeout(150)
        scroll = page.locator("#s-body").evaluate("e=>({top:e.scrollTop,h:e.clientHeight,sh:e.scrollHeight})")
        check(scroll["sh"] > scroll["h"] + 200, f"fixture is not long: {scroll}")
        check(scroll["top"] > 100 if position == "bottom" else scroll["top"] == 0, f"scroll failed: {scroll}")
        box = header.bounding_box()
        check(-1 <= box["y"] <= 60 and abs(box["y"] - original["y"]) <= 1, f"header scrolled away: {box}")
        check(box["y"] + box["height"] < 844 and box["x"] >= -1, f"header outside viewport: {box}")
        hit = page.locator("#s-more").evaluate("e=>{const r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}")
        check(hit, "header more button covered after scrolling")
        shot(page, "hdr-" + position, engine, lang, surface, theme)
    page.locator("#s-more").click()
    menu = page.locator('.mn-root [role="menu"]')
    expect(menu).to_be_visible()
    # Visibility starts at scale(.5); measure targets after the real 430ms
    # spring and staggered row transforms have completed.
    menu.evaluate("e=>Promise.all(e.getAnimations({subtree:true}).map(a=>a.finished.catch(()=>{})))")
    expect(page.locator("#s-more")).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#act-sheet")).to_be_hidden()
    box = menu.bounding_box()
    check(box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= 390 and box["y"] + box["height"] <= 844,
          f"menu outside viewport: {box}")
    rows = menu.get_by_role("menuitem")
    check(rows.count() >= 4, "missing legacy menu actions")
    check(all(r.bounding_box()["height"] >= 44 for r in rows.all()), "menu row below 44px")
    backdrop = page.locator(".mn-root .mn-back")
    expect(backdrop).to_be_visible()
    check(backdrop.evaluate("""e=>{const s=getComputedStyle(e);return +s.opacity>0 &&
      (s.backdropFilter!=='none'||s.webkitBackdropFilter!=='none'||!['transparent','rgba(0, 0, 0, 0)'].includes(s.backgroundColor));}"""),
          "backdrop neither dims nor blurs")
    watch = menu.locator('[data-k="watch"] small')
    value = watch.inner_text()
    menu.locator('[data-k="watch"]').click()
    expect(menu).to_be_visible()
    expect(watch).not_to_have_text(value)
    expect(watch).to_have_text(("включены" if lang == "ru" else "on"))
    shot(page, "hdr-menu", engine, lang, surface, theme)
    page.locator("#s-more").focus()
    page.keyboard.press("ArrowDown")
    expect(rows.first).to_be_focused()
    page.keyboard.press("End")
    expect(rows.last).to_be_focused()
    page.keyboard.press("Home")
    expect(rows.first).to_be_focused()
    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()
    expect(page.locator("#s-more")).to_be_focused()
    expect(page.locator("#s-more")).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#session-screen")).to_be_visible()
    page.locator("#s-more").click()
    expect(menu).to_be_visible()
    # Choose an actual outside point; the catch layer can own backdrop clicks.
    page.mouse.click(8, 830)
    expect(menu).to_be_hidden()
    expect(page.locator("#session-screen")).to_be_visible()
    page.locator("#s-more").click()
    expect(menu).to_be_visible()
    page.evaluate("window.dispatchEvent(new PopStateEvent('popstate', {state:null}))")
    page.wait_for_timeout(500)
    expect(menu).to_be_hidden()
    expect(page.locator("#session-screen")).to_be_visible()
    expansion = page.evaluate(VISIBLE_RECT)
    check(expansion["clip"] == "none" and abs(expansion["p"] - 1) < .001,
          f"menu popstate also collapsed the Clio session: {expansion}")
    page.locator("#s-more").click()
    expect(menu).to_be_visible()
    if surface == "telegram":
        check(page.evaluate("typeof window.__hdrBack==='function'"), "Telegram BackButton callback not registered")
        page.evaluate("window.__hdrBack()")
    else:
        # Invoke the real back control even though the menu catch layer owns
        # pointer input. This tests the same product navigation handler.
        back.evaluate("e=>e.click()")
    expect(menu).to_be_hidden()
    expect(page.locator("#session-screen")).to_be_visible()
    if surface == "telegram":
        page.evaluate("window.__hdrBack()")
    else:
        back.click()
    expect(page.locator("#session-screen")).to_be_hidden()


MORPH_INIT = """(() => {
  const animate0=Element.prototype.animate;
  Element.prototype.animate=function(frames,options) {
    const a=animate0.call(this,frames,options);
    if(JSON.stringify(frames).includes('--lo-p')) {
      a.playbackRate=.1; a.pause(); a.currentTime=0;
      window.__hdrMorph=a;
    }
    return a;
  };
})()"""
VISIBLE_RECT = """() => {
  const e=document.getElementById('session-screen'), r=e.getBoundingClientRect();
  const clip=getComputedStyle(e).clipPath;
  let inset=[0,0,0,0];
  if(clip.startsWith('inset(')) {
    let parts=clip.slice(6).split(' round ')[0].replace(')','').trim().split(/\\s+/);
    const values=parts.map((s,i)=>parseFloat(s)*(s.endsWith('%')?([r.height,r.width][i%2]/100):1));
    inset=values.length===1?[values[0],values[0],values[0],values[0]]:
      values.length===2?[values[0],values[1],values[0],values[1]]:
      values.length===3?[values[0],values[1],values[2],values[1]]:values;
  }
  return {x:r.x+inset[3],y:r.y+inset[0],width:r.width-inset[1]-inset[3],
    height:r.height-inset[0]-inset[2],clip,p:+getComputedStyle(document.documentElement).getPropertyValue('--lo-p')};
}"""


def morph(page, ctx, engine, lang, surface, theme):
    page.add_init_script(MORPH_INIT)
    ready(page)
    card = page.locator(f'#list .card[data-id="{SID}"]')
    before = card.bounding_box()
    card.click()
    page.wait_for_function("!!window.__hdrMorph")
    duration = page.evaluate("+window.__hdrMorph.effect.getTiming().duration")
    check(duration > 300, f"morph animation unexpectedly absent/short: {duration}")
    frames = []
    for elapsed in (300, 1500, 3000):
        page.evaluate("ms=>window.__hdrMorph.currentTime=ms*.1", elapsed)
        page.evaluate("()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
        box = page.evaluate(VISIBLE_RECT)
        frames.append(box)
        check(box["width"] > 0 and box["height"] > 0 and 0 < box["p"] <= 1, f"invalid morph frame: {box}")
        shot(page, f"hdr-morph-{elapsed}", engine, lang, surface, theme)
    first = frames[0]
    check(first["width"] * first["height"] < 390 * 844 * .99, f"first frame already fullscreen: {frames}")
    check(first["x"] < before["x"] + before["width"] and first["x"] + first["width"] > before["x"] and
          first["y"] < before["y"] + before["height"] and first["y"] + first["height"] > before["y"],
          f"morph does not originate at clicked card: {before} -> {first}")
    check(all(b["width"] * b["height"] >= a["width"] * a["height"] - 1 for a, b in zip(frames, frames[1:])),
          f"morph does not grow monotonically: {frames}")
    check(frames[-1]["height"] > first["height"] + 20, f"morph never grew: {frames}")
    page.evaluate("window.__hdrMorph.finish()")
    page.wait_for_timeout(400)
    final = page.evaluate(VISIBLE_RECT)
    check(abs(final["x"]) <= 1 and abs(final["y"]) <= 1 and abs(final["width"] - 390) <= 1 and abs(final["height"] - 844) <= 1,
          f"morph not fullscreen at slowed end {duration/.1}ms: {final}")
    expect(page.locator("#session-screen > header.session-header")).to_be_visible()
    print(f"  morph real source={duration:g}ms, 0.1x end={duration/.1:g}ms; frames=300/1500/3000ms", flush=True)


def cookie(ctx, name, value):
    ctx.add_cookies([{"name": name, "value": value, "url": URL}])


def visible_event(page):
    page.evaluate("""() => {
      Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});
      Object.defineProperty(document,'visibilityState',{configurable:true,get:()=> 'visible'});
      document.dispatchEvent(new Event('visibilitychange'));
    }""")


def changed(page, ctx, version):
    cookie(ctx, "app_version", version)
    visible_event(page)


def update(page, ctx, engine, lang, surface, theme):
    opened(page)
    page.evaluate("window.__hdrMarker='survives-until-reload'")
    heads = page.evaluate("window.__hdrHeads.length")
    visible_event(page)
    page.wait_for_function(f"n=>window.__hdrHeads.length>=n+{len(ASSETS)}", arg=heads)
    page.wait_for_timeout(200)
    check(page.evaluate("window.__hdrMarker") == "survives-until-reload", "unchanged version reloaded")
    for failure in ("503", "network"):
        cookie(ctx, "app_head_error", failure)
        heads = page.evaluate("window.__hdrHeads.length")
        visible_event(page)
        page.wait_for_function(f"n=>window.__hdrHeads.length>=n+{len(ASSETS)}", arg=heads)
        page.wait_for_timeout(200)
        check(page.evaluate("window.__hdrMarker") == "survives-until-reload", f"{failure} triggered reload")
        expect(page.locator("#toast")).to_be_hidden()
    cookie(ctx, "app_head_error", "")
    # An unchanged valid response after failure must not corrupt the baseline.
    heads = page.evaluate("window.__hdrHeads.length")
    visible_event(page)
    page.wait_for_function(f"n=>window.__hdrHeads.length>=n+{len(ASSETS)}", arg=heads)
    with page.expect_navigation(wait_until="load"):
        changed(page, ctx, "v2")
    check(page.evaluate("typeof window.__hdrMarker") == "undefined", "changed version did not reload")
    opened_after_reload = page.locator("#session-screen").is_visible()
    if not opened_after_reload:
        page.locator(f'#list .card[data-id="{SID}"]').click()
        page.wait_for_timeout(1000)
    page.wait_for_function(f"window.__hdrHeads.length>={len(ASSETS)}")
    text = page.locator("#s-text")
    text.fill("Do not discard this draft")
    text.evaluate("e=>e.blur()")
    page.evaluate("window.__hdrMarker='draft'")
    changed(page, ctx, "version-three")
    expect(page.locator("#toast")).to_contain_text("Доступно обновление" if lang == "ru" else "Update available")
    action = page.locator("#toast .ta")
    expect(action).to_have_text("Обновить" if lang == "ru" else "Update")
    check(page.evaluate("window.__hdrMarker") == "draft", "draft caused automatic reload")
    expect(text).to_have_value("Do not discard this draft")
    with page.expect_navigation(wait_until="load"):
        action.click()
    check(page.evaluate("typeof window.__hdrMarker") == "undefined", "update action did not reload")


def interval_and_busy(page, ctx, engine, lang, surface, theme):
    opened(page)
    page.evaluate("window.__hdrMarker='busy'")
    intervals = page.evaluate("window.__hdrIntervals.length")
    check(intervals == 1, f"expected one real 120s update interval, got {intervals}")
    heads = page.evaluate("window.__hdrHeads.length")
    page.evaluate("""() => {
      Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});
      Object.defineProperty(document,'visibilityState',{configurable:true,get:()=> 'hidden'});
      window.__hdrIntervals[0]();
    }""")
    page.wait_for_timeout(150)
    check(page.evaluate("window.__hdrHeads.length") == heads, "hidden interval fetched versions")
    visible_event(page)
    page.wait_for_function(f"n=>window.__hdrHeads.length>=n+{len(ASSETS)}", arg=heads)
    # Focus alone blocks an automatic reload even with no draft.
    page.locator("#s-text").focus()
    changed(page, ctx, "focused-version")
    expect(page.locator("#toast .ta")).to_be_visible()
    check(page.evaluate("window.__hdrMarker") == "busy", "focused input reloaded")
    page.locator("#s-text").evaluate("e=>e.blur()")
    # Reload resets version state; test the menu guard independently.
    page.reload(wait_until="load")
    page.wait_for_function(f"window.__hdrHeads.length>={len(ASSETS)}")
    if not page.locator("#session-screen").is_visible():
        page.locator(f'#list .card[data-id="{SID}"]').click()
        page.wait_for_timeout(1000)
    page.locator("#s-more").click()
    expect(page.locator('.mn-root [role="menu"]')).to_be_visible()
    page.evaluate("window.__hdrMarker='menu'")
    cookie(ctx, "app_version", "a-longer-menu-version")
    heads = page.evaluate("window.__hdrHeads.length")
    page.evaluate("window.__hdrIntervals[0]()")
    page.wait_for_function(f"n=>window.__hdrHeads.length>=n+{len(ASSETS)}", arg=heads)
    expect(page.locator("#toast .ta")).to_be_visible()
    check(page.evaluate("window.__hdrMarker") == "menu", "open menu interval reloaded")
    expect(page.locator('.mn-root [role="menu"]')).to_be_visible()


def question_busy(page, ctx, engine, lang, surface, theme):
    opened(page)
    root = page.locator('.lab-ask-root[data-state="open"]')
    expect(root).to_be_visible()
    page.evaluate("window.__hdrMarker='question'")
    changed(page, ctx, "question-version")
    expect(page.locator("#toast .ta")).to_be_visible()
    check(page.evaluate("window.__hdrMarker") == "question", "open question/permission reloaded")
    expect(root).to_be_visible()


def permission_busy(page, ctx, engine, lang, surface, theme):
    question_busy(page, ctx, engine, lang, surface, theme)
    expect(page.locator('.lab-ask-root.permission[data-state="open"]')).to_be_visible()


def telegram_init(theme):
    # Stub only host integration; product and Clio code remain unmodified.
    return """window.Telegram={WebApp:{initData:'synthetic',colorScheme:%s,
      ready(){},expand(){},disableVerticalSwipes(){},onEvent(){},offEvent(){},
      BackButton:{show(){},hide(){},onClick(fn){window.__hdrBack=fn;},offClick(){}},
      MainButton:{show(){},hide(){},setText(){},onClick(){},offClick(){},setParams(){}},
      HapticFeedback:{impactOccurred(){},notificationOccurred(){},selectionChanged(){}}}};""" % json.dumps(theme)


def main():
    important = len(re.findall(r"!important\b", (APP / "index.html").read_text()))
    check(important <= 7, f"index.html !important count {important} > 7")
    engines = [v.strip() for v in os.environ.get("BROWSERS", "chromium,webkit").split(",") if v.strip()]
    check(engines and all(e in ("chromium", "webkit") for e in engines), "invalid BROWSERS")
    failures = count = 0
    with tempfile.TemporaryDirectory(prefix="cc-header-") as temporary:
        datafile = Path(temporary) / "demo.json"
        write_fixture(datafile, fixture())
        env = dict(os.environ, APP_DIR=str(APP), DATA_FILE=str(datafile), PORT=str(PORT))
        for key in ("FAIL_SESSIONS", "APP_VERSION"):
            env.pop(key, None)
        server = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=env, stdout=subprocess.DEVNULL)
        try:
            for _ in range(50):
                check(server.poll() is None, "stand exited before readiness")
                try:
                    urllib.request.urlopen(URL + "/api/sessions", timeout=.3).close()
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("stand readiness timed out")
            with sync_playwright() as pw:
                for engine in engines:
                    browser = getattr(pw, engine).launch()
                    try:
                        for theme in ("light", "dark"):
                            for lang in ("en", "ru"):
                                for surface in ("pwa", "telegram"):
                                    for test in (header_menu, morph, update, interval_and_busy, question_busy, permission_busy):
                                        data = fixture()
                                        if test is question_busy:
                                            data["session"][SID]["question"] = deepcopy(BASE["session"][SID]["question"])
                                        elif test is permission_busy:
                                            data["session"][SID]["perm"] = {
                                                "kind": "sdk", "req": "header-permission", "title": "Bash",
                                                "body": "ls -la", "path": "/synthetic", "summary": "", "note": "",
                                                "question": "", "selected": 1,
                                                "options": [{"n": 1, "label": "Allow"}, {"n": 2, "label": "Deny"}]}
                                        write_fixture(datafile, data)
                                        ctx = browser.new_context(viewport={"width": 390, "height": 844},
                                            device_scale_factor=2, is_mobile=True, has_touch=True,
                                            locale=lang + ("-RU" if lang == "ru" else "-US"),
                                            color_scheme=theme, reduced_motion="no-preference", service_workers="block")
                                        ctx.add_init_script("localStorage.setItem('cc-lang'," + json.dumps(lang) + ")")
                                        ctx.add_init_script(INIT)
                                        ctx.route("https://telegram.org/**", lambda r: r.fulfill(body=""))
                                        if surface == "telegram":
                                            ctx.add_init_script(telegram_init(theme))
                                        page = ctx.new_page()
                                        page._hdr_host_theme = theme if surface == "telegram" else None
                                        errors = []
                                        page.on("pageerror", lambda error: errors.append(str(error)))
                                        label = f"{engine}/{theme}/{lang}/{surface}/{test.__name__}"
                                        count += 1
                                        try:
                                            test(page, ctx, engine, lang, surface, theme)
                                            page.wait_for_timeout(100)
                                            check(not errors, f"pageerror: {errors}")
                                            print("PASS " + label, flush=True)
                                        except Exception as error:
                                            failures += 1
                                            print("FAIL " + label + ": " + str(error), flush=True)
                                        finally:
                                            ctx.close()
                    finally:
                        browser.close()
        finally:
            server.terminate()
            server.wait(timeout=5)
    print(f"TOTAL {count - failures} PASS, {failures} FAIL", flush=True)
    if os.environ.get("RUN_REGRESSIONS") == "1":
        for script in ("check_defects.py", "check_perm.py", "regress_open.py"):
            result = subprocess.run([sys.executable, str(ROOT / script)], env=dict(os.environ, APP_DIR=str(APP)))
            failures += int(result.returncode != 0)
    return int(bool(failures))


if __name__ == "__main__":
    try:
        result = main()
    except Exception as error:
        print(f"FAIL INFRA {type(error).__name__}: {error}", flush=True)
        print("TOTAL BLOCKED: browser checks not completed", flush=True)
        result = 2
    sys.exit(result)
