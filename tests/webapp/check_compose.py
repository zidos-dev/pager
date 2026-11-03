"""Поле сессии и пилюли под шапкой (жалоба владельца 19.10: «баг при многострочном наборе», «обрезается текст про сабтаски в пиллах»).
Поле плавает над лентой (редизайн «В»): при росте поля на длинной истории последнее сообщение не уходит под поле,
а прокрученную вверх ленту рост поля не дёргает. Пилюли «Сабтаски» и «Продолжение» и теги строк списка показывают текст целиком."""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).parent; PORT=int(os.environ.get("PORT","18989")); U=f"http://127.0.0.1:{PORT}"
APP=os.environ.get("APP_DIR")
SID="b2d4f6a8"  # без вопроса: лист вопроса не накрывает поле
D=json.loads((ROOT.parent/"stand"/"demo2.json").read_text())
S=D["session"][SID]; S["history"]=S["history"]*15
S["subagents_count"]={"working":1,"total":3}; S["prev"]={"id":"b2d4f6a8","name":"Write release notes for v2.3 and update the changelog"}; S["k"]=2
for x in D["sessions"]["sessions"]:
    if x["id"]=="c3e5a7b9": x["group"]="old"; x["into"]={"id":"b2d4f6a8","name":"Write release notes for v2.3 and update the changelog"}
data=Path(tempfile.gettempdir())/f"compose-{PORT}.json"; data.write_text(json.dumps(D))
env=dict(os.environ,DATA_FILE=str(data),PORT=str(PORT))
srv=subprocess.Popen([sys.executable,str(ROOT/"stand_server.py")],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U+"/api/sessions",timeout=.3).close(); break
    except OSError: time.sleep(.1)
fails=0
def check(ok,name,detail=""):
    global fails; fails+=0 if ok else 1; print(("PASS" if ok else "FAIL"),name,detail,flush=True)
GEO="""()=>{const c=document.querySelector('#session-screen .compose').getBoundingClientRect(),b=document.getElementById('s-body');
 const last=[...document.querySelectorAll('#s-hist > *')].filter(e=>e.offsetHeight).pop().getBoundingClientRect();
 return {top:Math.round(c.top),last:Math.round(last.bottom),st:Math.round(b.scrollTop)}}"""
CUT="""id=>{const e=document.getElementById(id),p=e.parentNode.getBoundingClientRect(),r=e.getBoundingClientRect();
 return {w:e.scrollWidth-e.clientWidth,h:e.scrollHeight-e.clientHeight,out:Math.round(r.right-p.right),txt:e.textContent}}"""
try:
  with sync_playwright() as pw:
    for eng in ("chromium","webkit"):
        b=getattr(pw,eng).launch(); ctx=b.new_context(viewport={"width":380,"height":740},locale="ru-RU")
        ctx.route("https://telegram.org/**",lambda r:r.fulfill(body=""))
        p=ctx.new_page(); errs=[]; p.on("pageerror",lambda e:errs.append(str(e)))
        p.goto(U+"/app/"); p.wait_for_timeout(1500)
        p.evaluate("()=>document.querySelector('#chips .chip[data-f=\"arch\"]').click()"); p.wait_for_timeout(500)
        tg=p.evaluate("()=>[...document.querySelectorAll('#list .tag')].map(e=>({w:e.scrollWidth-e.clientWidth,txt:e.textContent}))")
        check(tg and all(x["w"]<=1 for x in tg),f"{eng} list tags show their whole text",json.dumps(tg,ensure_ascii=False)[:200])
        p.evaluate("()=>document.querySelector('#chips .chip[data-f=\"act\"]').click()"); p.wait_for_timeout(500)
        p.locator(f'#list [data-id="{SID}"]').first.click(); p.wait_for_timeout(1800)
        for pid in ("s-subs","s-chain"):
            c=p.evaluate(CUT,pid)
            check(c["w"]<=1 and c["h"]<=1 and c["out"]<=1,f"{eng} pill #{pid} shows its whole text",json.dumps(c,ensure_ascii=False))
        ta=p.locator("#s-text"); ta.click(); p.wait_for_timeout(500)
        g0=p.evaluate(GEO); check(g0["last"]<=g0["top"]+1,f"{eng} at end: last message above the field",json.dumps(g0))
        for i in range(1,9):
            p.keyboard.type(f"строка {i} текста"); p.keyboard.press("Enter") if i<8 else None; p.wait_for_timeout(120)
        p.wait_for_timeout(300); g=p.evaluate(GEO)
        check(g["last"]<=g["top"]+1,f"{eng} 8 lines: last message stays above the grown field",json.dumps(g))
        p.keyboard.press("Control+a"); p.keyboard.press("Backspace"); p.wait_for_timeout(400); g=p.evaluate(GEO)
        check(g["last"]<=g["top"]+1,f"{eng} cleared: last message above the field",json.dumps(g))
        # лента прокручена вверх: рост поля её не двигает
        p.evaluate("document.getElementById('s-body').scrollTop-=600"); p.wait_for_timeout(300); s0=p.evaluate(GEO)["st"]
        for i in range(1,4): p.keyboard.type(f"ещё {i}"); p.keyboard.press("Enter"); p.wait_for_timeout(120)
        p.wait_for_timeout(300); s1=p.evaluate(GEO)["st"]
        check(abs(s1-s0)<=2,f"{eng} scrolled up: growing field does not move the history",f"{s0}->{s1}")
        check(not errs,f"{eng} no page errors",str(errs[:2]))
        b.close()
finally:
    srv.terminate()
print("TOTAL", "FAIL" if fails else "PASS"); sys.exit(1 if fails else 0)
