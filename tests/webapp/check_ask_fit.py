"""Question sheet with «own answer» open and the keyboard up (--vvh shrinks): option cards keep their full height and the
list scrolls; card text never spills over the question or the next card, the question header is not clipped
(owner 20.10 10:44, IMG_5138: grid rows shrank below content because .lab-ask-opt sets an explicit min-height).

Usage: PORT=19303 python3 check_ask_fit.py   (APP checkout = this repo; chromium + webkit, 440×956, --vvh none/480/400)
"""
import json, os, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
APP=Path(__file__).resolve().parents[2]; PORT=int(os.environ.get("PORT","18983")); URL=f"http://127.0.0.1:{PORT}"; SID="a1c3e5f7"
d=json.loads((APP/"tests/stand/demo2.json").read_text()); q=d["session"][SID]["question"]
q["question"]="Как сделать на Mac правильно?"
q["options"]=[{"label":"Как на N2X1 (Recommended)","description":"Разложенные папки (Доки, Учёба, Работа…) перенести в «Документы», код и папки агентов — в «Проекты/Код» вне iCloud, чтобы не гонять 800 тыс. файлов через облако. 36 файлов из «Разобрать» разложить, пустые папки убрать","marked":False},
 {"label":"Оставить в корне iCloud","description":"Разложенное остаётся в корне iCloud Drive; код из «Документов» вынести в «Проекты/Код», «Разобрать» и пустые папки дочистить","marked":False}]
tmp=tempfile.mkdtemp(); f=Path(tmp)/"d.json"; f.write_text(json.dumps(d))
srv=subprocess.Popen([sys.executable,str(APP/"tests/webapp/stand_server.py")],env=dict(os.environ,DATA_FILE=str(f),PORT=str(PORT)),stdout=subprocess.DEVNULL)
bad=0
try:
  for _ in range(50):
    try: urllib.request.urlopen(URL+"/api/sessions",timeout=.3).close(); break
    except OSError: time.sleep(.1)
  with sync_playwright() as pw:
    for eng in ("chromium","webkit"):
     for vvh in (None, 480, 400):
      b=getattr(pw,eng).launch(); ctx=b.new_context(viewport={"width":440,"height":956},is_mobile=eng=="chromium",has_touch=True); p=ctx.new_page()
      p.goto(URL+"/app/?theme=tg-light"); p.locator(f'.card[data-id="{SID}"]').click()
      r=p.locator('.lab-ask-root[data-state="open"]'); expect(r).to_be_visible(); p.wait_for_timeout(1000)
      r.locator('.lab-ask-btn[data-act="own"]').click(); expect(r.locator(".lab-ask-own-text")).to_be_focused(); p.wait_for_timeout(700)
      if vvh: p.locator("html").evaluate(f"e=>e.style.setProperty('--vvh','{vvh}px')"); p.wait_for_timeout(700)
      hd=p.evaluate("()=>{const h=document.querySelector('.lab-ask-root[data-state=open] .lab-ask-head');return h.scrollHeight-h.clientHeight}"); bad+=hd>1
      print(eng,vvh,"head clip",hd)
      res=p.evaluate("""()=>[...document.querySelectorAll('.lab-ask-root[data-state=open] .lab-ask-opt')].map(o=>{const r=o.getBoundingClientRect(),t=o.querySelector('.lab-ask-tx').getBoundingClientRect();return [Math.round(r.height),Math.round(t.height),t.top<r.top-1||t.bottom>r.bottom+1]})""")
      lst=p.evaluate("()=>{const l=document.querySelector('.lab-ask-root[data-state=open] .lab-ask-list');return [l.clientHeight,l.scrollHeight]}")
      over=any(x[2] for x in res); bad+=over
      print(eng,vvh,"opts(h,txt,overflow)",res,"list(client,scroll)",lst,"OVERLAP" if over else "ok")
finally: srv.terminate()
print("TOTAL", "FAIL" if bad else "PASS"); sys.exit(1 if bad else 0)
