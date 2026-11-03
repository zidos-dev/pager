"""Отклик без ожидания сервера (жалоба владельца 19.10: «лаг при отправке и при закрытии сессии»).
Отправка: сервер отвечает после вставки в tmux (стенд: send_delay=2 с) — поле пустеет сразу, пузырь виден сразу;
при отказе текст возвращается в поле. Закрытие: капсула фильтров возвращается вместе с морфом, а не после пружины навигации."""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).parent; PORT=int(os.environ.get("PORT","18988")); U=f"http://127.0.0.1:{PORT}"
data=Path(tempfile.gettempdir())/f"lag-{PORT}.json"; data.write_text((ROOT.parent/"stand"/"demo2.json").read_text())
srv=subprocess.Popen([sys.executable,str(ROOT/"stand_server.py")],env=dict(os.environ,DATA_FILE=str(data),PORT=str(PORT)),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U+"/api/sessions",timeout=.3).close(); break
    except OSError: time.sleep(.1)
SID="b2d4f6a8"  # сессия без вопроса: лист вопроса не накрывает поле
fails=0
def check(ok,name,detail=""):
    global fails; fails+=0 if ok else 1; print(("PASS" if ok else "FAIL"),name,detail,flush=True)
try:
  with sync_playwright() as pw:
    for eng in ("chromium","webkit"):
        b=getattr(pw,eng).launch(); ctx=b.new_context(viewport={"width":390,"height":844},locale="en-US")
        ctx.route("https://telegram.org/**",lambda r:r.fulfill(body=""))
        p=ctx.new_page(); errs=[]; p.on("pageerror",lambda e:errs.append(str(e)))
        p.goto(U+"/app/"); p.wait_for_timeout(1500)
        # закрытие: капсула уходит под сессией и должна вернуться до конца морфа (355 мс), а не после пружины
        p.locator(f'#list .card[data-id="{SID}"]').click(); p.wait_for_timeout(1500)
        check(p.evaluate("()=>document.getElementById('lab-fcap').classList.contains('lf-away')"),f"{eng} capsule away under session")
        back=p.evaluate("""()=>new Promise(res=>{const c=document.getElementById('lab-fcap'),t0=performance.now();
          const mo=new MutationObserver(()=>{if(!c.classList.contains('lf-away')){mo.disconnect();res(performance.now()-t0);}});
          mo.observe(c,{attributes:true,attributeFilter:['class']});
          document.querySelector('#session-screen [data-back]').click();  // как BackButton Telegram: goBack → pop() с пружиной
          setTimeout(()=>{mo.disconnect();res(-1);},3000);})""")
        check(0<=back<150,f"{eng} capsule returns with the close morph",f"{back:.0f} ms")
        p.wait_for_timeout(1500)
        # отправка: поле пустеет сразу, не ждёт ответа сервера
        p.locator(f'#list .card[data-id="{SID}"]').click(); p.wait_for_timeout(1500)
        ctx.add_cookies([{"name":"send_delay","value":"2","url":U}])
        ta=p.locator("#s-text"); ta.fill("lag probe one"); p.locator("#s-send").click(); p.wait_for_timeout(250)
        check(ta.input_value()=="",f"{eng} field empties before the server answers",repr(ta.input_value()))
        check(p.locator("#s-hist .me.pending",has_text="lag probe one").count()==1,f"{eng} pending bubble shown at once")
        p.wait_for_timeout(2500)
        check(ta.input_value()=="",f"{eng} field stays empty after ok")
        check(p.locator("#s-hist .it.enter",has_text="lag probe one").count()==0,f"{eng} real message replaces the bubble without a second slide-in")
        # отказ: текст возвращается в поле, пузырь убран
        ctx.add_cookies([{"name":"send_reply","value":"fail","url":U},{"name":"send_delay","value":"1","url":U}])
        ta.fill("lag probe two"); p.locator("#s-send").click(); p.wait_for_timeout(200)
        check(ta.input_value()=="",f"{eng} failing send also empties at once")
        p.wait_for_timeout(1800)
        check(ta.input_value()=="lag probe two",f"{eng} text restored after failure",repr(ta.input_value()))
        check(p.locator("#s-hist .me.pending",has_text="lag probe two").count()==0,f"{eng} failed bubble removed")
        check(not errs,f"{eng} no page errors",str(errs[:2]))
        b.close()
finally:
    srv.terminate()
print("TOTAL", "FAIL" if fails else "PASS"); sys.exit(1 if fails else 0)
