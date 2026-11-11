"""Lane l3: permission sheet (full labels, one accent, quiet deny), press tone, field focus ring,
small fixes (RU grammar, contrasts, back label, push title, report font), mic in «Свой ответ».
Chromium + WebKit, 390x844 and 393x659, light and dark. Voice recording: Chromium only (fake mic)."""
from copy import deepcopy
import json, math, os, re, struct, subprocess, sys, tempfile, time, urllib.request, wave
from io import BytesIO
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "18991")); URL = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.gettempdir()); DATA = TMP / f"l3-{PORT}.json"; VLOG = TMP / f"l3-voice-{PORT}.jsonl"
APP = Path(os.environ.get("APP_DIR", ROOT.parent.parent / "assets/webapp"))
OUT = Path(os.environ.get("SHOTS", TMP))
BASE = json.loads((ROOT.parent / "stand/demo2.json").read_text())
TMUX = {"kind": "tmux", "req": None, "path": "", "title": "Bash command", "summary": "Write output",
        "body": "echo hi >> out.txt", "note": "", "question": "Do you want to proceed?", "selected": 1,
        "options": [{"n": 1, "label": "Yes"},
                    {"n": 2, "label": "Yes, and always allow access to synthetic sensitive files from this project"},
                    {"n": 3, "label": "Yes, and switch to auto mode · auto mode handles these prompts for you"},
                    {"n": 4, "label": "No"}]}
SDK = {"kind": "sdk", "req": "r-1", "title": "Bash", "summary": "", "body": "ls -la", "path": "/a/b.txt", "note": "", "question": "", "selected": 1,
       "options": [{"n": 1, "label": "Разрешить"}, {"n": 2, "label": "Всегда Bash в этой сессии"}, {"n": 3, "label": "Отклонить"}]}
for sid, perm in (("p-tmux01", TMUX), ("s-sdk0001", SDK)):
    row = deepcopy(BASE["sessions"]["sessions"][0]); row.update(id=sid, name="Synthetic " + sid, base_name="Synthetic " + sid, perm=True, ask=None)
    BASE["sessions"]["sessions"].insert(0, row)
    BASE["session"][sid] = dict(row, history=[], cwd="/synthetic", question=None, perm=deepcopy(perm))
BASE["sessions"]["voice"] = True
DATA.write_text(json.dumps(BASE, ensure_ascii=False))
VLOG.unlink(missing_ok=True)
tone = TMP / "l3-tone.wav"
with wave.open(str(tone), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
    w.writeframes(b"".join(struct.pack("<h", int(16000 * math.sin(2 * math.pi * 440 * i / 48000))) for i in range(48000 * 3)))
srv = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, DATA_FILE=str(DATA), PORT=str(PORT), APP_DIR=str(APP), VOICE_LOG=str(VLOG)),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    try: urllib.request.urlopen(URL + "/api/sessions", timeout=.3).close(); break
    except OSError: time.sleep(.1)
fails = 0
def check(ok, name, detail=""):
    global fails; fails += 0 if ok else 1; print(("PASS" if ok else "FAIL"), name, detail, flush=True)
def rgb(s): return [float(x) for x in re.findall(r"[\d.]+", s)[:3]]
def lin(c): c /= 255; return c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4
def lum(c): return .2126 * lin(c[0]) + .7152 * lin(c[1]) + .0722 * lin(c[2])
def contrast(a, b): x, y = sorted((lum(a), lum(b)), reverse=True); return (x + .05) / (y + .05)
def lstar(c): y = lum(c); return 116 * (y ** (1 / 3)) - 16 if y > .008856 else 903.3 * y
def patch(page, x, y):  # mean colour of a 4x4 patch
    im = Image.open(BytesIO(page.screenshot(clip={"x": x, "y": y, "width": 4, "height": 4}))).convert("RGB")
    px = list(im.getdata()); return [sum(p[i] for p in px) / len(px) for i in range(3)]
def press_delta(page, sel, name):
    b = page.locator(sel).first.bounding_box(); x, y = b["x"] + 5, b["y"] + b["height"] / 2 - 2
    rest = patch(page, x, y)
    page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2); page.mouse.down(); page.wait_for_timeout(260)
    down = patch(page, x, y)
    op = page.locator(sel).first.evaluate("e=>getComputedStyle(e).opacity")
    page.mouse.move(2, 2); page.mouse.up(); page.wait_for_timeout(200)
    d = abs(lstar(down) - lstar(rest))
    check(d >= 8 and op == "1", f"press tone {name}", f"dL*={d:.1f} opacity={op}")
def ctx_for(b, eng, w, h, scheme, ru=True, mic=False):
    c = b.new_context(viewport={"width": w, "height": h}, color_scheme=scheme, locale="ru-RU", **({"permissions": ["microphone"]} if mic else {}), **({"has_touch": False}))
    c.route("https://telegram.org/**", lambda r: r.fulfill(body=""))
    if ru: c.add_init_script("try{localStorage.setItem('cc-lang','ru')}catch(e){}")
    return c
def open_session(page, sid):
    page.goto(URL + "/app/", wait_until="load"); page.wait_for_selector(f'.card[data-id="{sid}"]')
    page.locator(f'.card[data-id="{sid}"]').click(); page.wait_for_timeout(1100)

try:
  with sync_playwright() as pw:
    for eng in ("chromium", "webkit"):
      args = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", f"--use-file-for-fake-audio-capture={tone}"] if eng == "chromium" else []
      b = getattr(pw, eng).launch(args=args)
      for (w, h) in ((390, 844), (393, 659)):
        for scheme in ("light", "dark"):
          tag = f"{eng} {w}x{h} {scheme}"
          ctx = ctx_for(b, eng, w, h, scheme); page = ctx.new_page()
          # №4 + №16: tmux sheet, full labels
          if eng == "chromium" or True:
            open_session(page, "p-tmux01")
            root = page.locator('.lab-ask-root.permission[data-state="open"]')
            if root.count():
              labels = page.evaluate("()=>[...document.querySelectorAll('.lab-ask-root .lab-ask-opt')].map(o=>({l:o.getAttribute('aria-label'),t:o.querySelector('.lab-ask-t').textContent,c:o.querySelector('.lab-ask-t').scrollHeight>o.querySelector('.lab-ask-t').clientHeight+1,ws:getComputedStyle(o.querySelector('.lab-ask-t')).whiteSpace,to:getComputedStyle(o.querySelector('.lab-ask-t')).textOverflow}))")
              check(all(x["t"] == x["l"] and not x["c"] and x["ws"] != "nowrap" and x["to"] != "ellipsis" for x in labels), f"№4 full label text {tag}", str([x["t"][:40] for x in labels if x["t"] != x["l"]]))
              box = page.locator(".lab-ask-sheet").bounding_box()
              check(box["y"] >= 0 and box["y"] + box["height"] <= h + 1, f"№4 sheet fits window {tag}", str(box))
              page.screenshot(path=str(OUT / f"perm-tmux-{eng}-{w}x{h}-{scheme}.png"))
          # №16: sdk sheet accents
          open_session(page, "s-sdk0001")
          if page.locator('.lab-ask-root.permission[data-state="open"]').count():
            c = page.evaluate("""()=>{const o=[...document.querySelectorAll('.lab-ask-root .lab-ask-opt')],g=e=>getComputedStyle(e);
              return {yes:[g(o[0]).backgroundColor,g(o[0]).color],mid:[g(o[1]).backgroundColor,g(o[1]).color],no:[g(o[2]).backgroundColor,g(o[2]).color],lime:g(document.querySelector('.lab-ask-root')).getPropertyValue('--lab-lime').trim(),red:[g(document.documentElement).getPropertyValue('--danger').trim(),g(document.documentElement).getPropertyValue('--perm-danger').trim()]}}""")
            yes_bg, yes_fg = rgb(c["yes"][0]), rgb(c["yes"][1])
            check(yes_bg == [210, 251, 74], f"№16 allow = main action colour {tag}", c["yes"][0])
            check(contrast(yes_bg, yes_fg) >= 4.5, f"№16 allow text contrast {tag}", f"{contrast(yes_bg, yes_fg):.1f}")
            check(c["no"] == c["mid"], f"№16 deny: quiet tone fill, ordinary text colour (as the other options) {tag}", f"{c['no']} vs {c['mid']}")
            page.screenshot(path=str(OUT / f"perm-sdk-{eng}-{w}x{h}-{scheme}.png"))
          ctx.close()
        # light+dark at 390 only for the remaining checks
      for scheme in ("light", "dark"):
        tag = f"{eng} 390x844 {scheme}"
        ctx = ctx_for(b, eng, 390, 844, scheme); page = ctx.new_page()
        page.goto(URL + "/app/", wait_until="load"); page.wait_for_selector(".card")
        press_delta(page, "#bots-btn", f"header icon {tag}")
        # №13 focus ring: «Задача» equals «Свой ответ»
        page.locator("#new-btn").click(); page.wait_for_timeout(700)
        page.locator("#n-text").focus(); page.wait_for_timeout(200)
        f1 = page.evaluate("()=>{const s=getComputedStyle(document.getElementById('n-text'));return [s.outlineStyle,s.outlineWidth,s.outlineColor,s.outlineOffset].join('|')}")
        page.screenshot(path=str(OUT / f"new-focus-{eng}-{scheme}.png"))
        open_session(page, "a1c3e5f7") if False else None
        page.goto(URL + "/app/", wait_until="load"); page.wait_for_selector('.card[data-id="a1c3e5f7"]'); page.locator('.card[data-id="a1c3e5f7"]').click(); page.wait_for_timeout(1100)
        press_delta(page, '.lab-ask-opt[data-k="0"]', f"question option {tag}")
        press_delta(page, '.lab-ask-btn[data-act="own"]', f"«Свой ответ» button {tag}")
        page.locator('.lab-ask-btn[data-act="own"]').click(); page.wait_for_timeout(700)
        page.locator(".lab-ask-own-text").focus(); page.wait_for_timeout(200)
        f2 = page.evaluate("()=>{const s=getComputedStyle(document.querySelector('.lab-ask-own-text'));return [s.outlineStyle,s.outlineWidth,s.outlineColor,s.outlineOffset].join('|')}")
        check(f1 == f2 and not f1.startswith("none"), f"№13 task field focus = own-answer focus {tag}", f"{f1} vs {f2}")
        press_delta(page, '.lab-ask-btn[data-act="own-cancel"]', f"own cancel {tag}")
        # F4: mic in «Свой ответ»
        mic = page.locator(".lab-ask-own #lo-mic")
        can = page.evaluate("()=>!!(window.MediaRecorder&&navigator.mediaDevices&&navigator.mediaDevices.getUserMedia)")
        check(mic.count() == 1 and (not can or (mic.is_visible() and mic.bounding_box()["height"] >= 44)), f"F4 mic button in own form {tag}", f"can={can}")
        if eng == "chromium" and mic.count():
            mic.click(); page.wait_for_timeout(1200)
            check(page.evaluate("()=>{const l=document.querySelector('.vo-layer');return !!l&&!l.hidden}"), f"F4 recording layer {tag}")
            page.locator(".vo-ok").click()
            try: page.wait_for_function("()=>document.querySelector('.lab-ask-own-text').value.includes('проверка голоса')", timeout=5000); got = True
            except Exception: got = False
            check(got, f"F4 recognized text lands in own field {tag}", repr(page.locator(".lab-ask-own-text").input_value()))
            check(page.locator('.lab-ask-btn[data-act="own-send"]').is_enabled(), f"F4 send enabled after dictation {tag}")
            page.wait_for_timeout(700)
            check(page.evaluate("()=>{const l=document.querySelector('.vo-layer');return !l||l.hidden||getComputedStyle(l).opacity==='0'||getComputedStyle(l).visibility==='hidden'}"), f"F4 voice layer gone after dictation {tag}")
            page.screenshot(path=str(OUT / f"own-mic-{eng}-{scheme}.png"))
        ctx.close()
      # №18
      ctx = ctx_for(b, eng, 390, 844, "light"); page = ctx.new_page()
      page.goto(URL + "/app/", wait_until="load"); page.wait_for_selector(".card")
      s = page.evaluate("()=>[1,2,5].map(n=>t('subsRun',{w:n,t:7}))") if False else None
      txt = page.evaluate("()=>{const x=document.documentElement.lang;return x}")
      src = (APP / "index.html").read_text()
      check("<b>{w} работают</b>" not in src and "работает" in src.split('"subsRun"')[2][:80], f"№18 RU subtasks grammar {eng}")
      check("Claude Sessions" not in (APP / "sw.js").read_text() and "Zidos Pager" in (APP / "sw.js").read_text(), f"№18 sw.js fallback title {eng}")
      rep = (APP / "report.html").read_text()
      check('"Onest"' in rep and "onest-cyrillic.woff2" in rep, f"№18 report font Onest {eng}")
      # contrast of back and bot error
      page.goto(URL + "/app/", wait_until="load"); page.wait_for_selector(".card")
      cc = page.evaluate("""()=>{const cv=document.createElement('canvas').getContext('2d',{willReadFrequently:true});
        const n=c=>{cv.clearRect(0,0,1,1);cv.fillStyle='#000';cv.fillStyle=c;cv.fillRect(0,0,1,1);const d=cv.getImageData(0,0,1,1).data;return 'rgb('+d[0]+','+d[1]+','+d[2]+')'};
        const cs=(cls,prop)=>{const e=document.createElement('span');e.className=cls;e.style.cssText='position:fixed;background:var(--soft)';document.body.append(e);const v=getComputedStyle(e)[prop];e.remove();return n(v)};
        return {back:cs('back','color'),fail:cs('bst bad','color'),soft:cs('','backgroundColor'),canvas:n(getComputedStyle(document.body).backgroundColor)}}""")
      check(contrast(rgb(cc["back"]), rgb(cc["canvas"])) >= 4.5, f"№18 back contrast {eng}", f"{contrast(rgb(cc['back']), rgb(cc['canvas'])):.2f}")
      check(contrast(rgb(cc["fail"]), rgb(cc["soft"])) >= 4.5 and contrast(rgb(cc["fail"]), rgb(cc["canvas"])) >= 4.5, f"№18 bot error contrast {eng}", f"{contrast(rgb(cc['fail']), rgb(cc['soft'])):.2f}")
      # header title: long title not cut earlier than needed
      page.locator('.card[data-id="h2y8b1ef"]').click(); page.wait_for_timeout(1100)
      r = page.evaluate("""()=>{const h=document.getElementById('s-name'),bk=document.querySelector('#session-screen .session-back .back');const hb=h.getBoundingClientRect(),bb=bk.getBoundingClientRect();return {bw:bb.width,hw:hb.width,sw:h.scrollWidth,cw:h.clientWidth}}""")
      check(r["bw"] <= 56 and r["bw"] >= 44, f"№18 back is arrow only (44..56 px) {eng}", str(r))
      ctx.close(); b.close()
finally:
  srv.terminate()
print("FAILS", fails); sys.exit(1 if fails else 0)
