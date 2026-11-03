"""Микрофон в поле сессии (контракт: GET /api/sessions {voice}, POST /api/voice audio/* -> {ok, text}).
Кнопка #s-mic видна, только если сервер умеет распознавать и браузер умеет записывать, и только при пустом поле
(при тексте — «Отправить»). Запись: тап — пишет (aria-pressed=true), второй тап — отправляет; текст встаёт в поле
на правку, ничего не отправляется в сессию. Ошибка распознавания — тост, поле не меняется.
Chromium с поддельным микрофоном; в десктопном WebKit поддельного устройства нет — там только видимость кнопки."""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).parent; PORT=int(os.environ.get("PORT","18987")); U=f"http://127.0.0.1:{PORT}"
TMP=Path(tempfile.gettempdir()); data=TMP/f"voice-{PORT}.json"; vlog=TMP/f"voice-log-{PORT}.jsonl"; slog=TMP/f"voice-send-{PORT}.jsonl"
base=json.loads((ROOT.parent/"stand"/"demo2.json").read_text())
def put(voice):
    d=json.loads(json.dumps(base)); d["sessions"]["voice"]=voice; data.write_text(json.dumps(d))
put(True)
for f in (vlog,slog): f.unlink(missing_ok=True)
srv=subprocess.Popen([sys.executable,str(ROOT/"stand_server.py")],env=dict(os.environ,DATA_FILE=str(data),PORT=str(PORT),VOICE_LOG=str(vlog),SEND_LOG=str(slog)),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U+"/api/sessions",timeout=.3).close(); break
    except OSError: time.sleep(.1)
fails=0
def check(ok,name,detail=""):
    global fails; fails+=0 if ok else 1; print(("PASS" if ok else "FAIL"),name,detail,flush=True)
def lines(f): return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
def shown(p,sel): return p.evaluate(f"()=>{{const e=document.querySelector('{sel}');return !!e&&!e.hidden&&getComputedStyle(e).display!=='none'&&e.getBoundingClientRect().width>0}}")
def open_session(ctx):
    p=ctx.new_page(); errs=[]; p.on("pageerror",lambda e:errs.append(str(e)))
    p.goto(U+"/app/"); p.wait_for_timeout(1500)
    p.locator('#list .card[data-id="b2d4f6a8"]').click(); p.wait_for_timeout(1500)  # сессия без вопроса: лист вопроса накрывает поле целиком
    return p,errs
try:
  with sync_playwright() as pw:
    for eng in ("chromium","webkit"):
        args=["--use-fake-device-for-media-stream","--use-fake-ui-for-media-stream"] if eng=="chromium" else []
        b=getattr(pw,eng).launch(args=args)
        ctx=b.new_context(viewport={"width":390,"height":844},locale="ru-RU",**({"permissions":["microphone"]} if eng=="chromium" else {}))
        ctx.route("https://telegram.org/**",lambda r:r.fulfill(body=""))
        # сервер без распознавания — кнопки нет
        put(False); p,errs=open_session(ctx)
        check(not shown(p,"#s-mic"),f"{eng} no mic when voice=false"); p.close(); put(True)
        p,errs=open_session(ctx)
        can=p.evaluate("()=>!!(window.MediaRecorder&&navigator.mediaDevices&&navigator.mediaDevices.getUserMedia)")
        if not can:
            check(not shown(p,"#s-mic"),f"{eng} no mic without MediaRecorder"); b.close(); continue
        check(shown(p,"#s-mic") and not shown(p,"#s-send"),f"{eng} empty field: mic instead of send")
        p.locator("#s-text").fill("hi"); p.wait_for_timeout(100)
        check(shown(p,"#s-send") and not shown(p,"#s-mic"),f"{eng} text: send instead of mic")
        p.locator("#s-text").fill(""); p.wait_for_timeout(100)
        check(shown(p,"#s-mic"),f"{eng} cleared field: mic back")
        if eng!="chromium":
            print(f"SKIP {eng} recording: no fake microphone in desktop {eng} (check on device)",flush=True); b.close(); continue
        mic=p.locator("#s-mic")
        mic.click(); p.wait_for_timeout(1200)
        check(mic.get_attribute("aria-pressed")=="true",f"{eng} recording state")
        # выкатка во время записи: автообновление ждёт, запись не теряется
        p.evaluate("window.__vMarker='alive'"); ctx.add_cookies([{"name":"app_version","value":"v2","url":U}])
        p.evaluate("()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'))}")
        p.wait_for_timeout(1500)
        check(p.evaluate("window.__vMarker")=="alive" and mic.get_attribute("aria-pressed")=="true",f"{eng} new version while recording: no reload, still recording")
        ctx.add_cookies([{"name":"app_version","value":"","url":U}])
        mic.click()
        try: p.wait_for_function("()=>document.getElementById('s-text').value.includes('проверка голоса')",timeout=5000); got=True
        except Exception: got=False
        v=lines(vlog)
        check(got,f"{eng} recognized text in field",repr(p.locator("#s-text").input_value()))
        check(len(v)==1 and v[0]["type"].startswith("audio/") and v[0]["size"]>0,f"{eng} one audio POST",str(v))
        check(mic.get_attribute("aria-pressed")!="true" and shown(p,"#s-send"),f"{eng} idle after recognition, send shown")
        check(not lines(slog),f"{eng} nothing sent to the session")
        # ошибка распознавания: тост, поле не трогаем
        p.locator("#s-text").fill(""); ctx.add_cookies([{"name":"voice_reply","value":"fail","url":U}])
        mic.click(); p.wait_for_timeout(800); mic.click(); p.wait_for_timeout(1500)
        toast=p.locator(".toast").last.inner_text() if p.locator(".toast").count() else ""
        check("unavailable" in toast.lower() or "недоступ" in toast.lower(),f"{eng} failure toast",repr(toast))
        check(p.locator("#s-text").input_value()=="",f"{eng} field untouched on failure")
        check(not errs,f"{eng} no page errors",str(errs[:2]))
        b.close()
finally:
    srv.terminate()
print("TOTAL", "FAIL" if fails else "PASS"); sys.exit(1 if fails else 0)
