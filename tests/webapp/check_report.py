"""Страница дневного отчёта /r/<token> (вне Telegram, доступ по токену): разделы в порядке Сайт → Прогнозы и данные →
Сервер → Прочее с числами; префикс feat(v2) — короткой меткой; заголовок коммита не исполняется как HTML; устаревшая
ссылка (404) и пустые сутки — понятным текстом. 390/1440 × светлая/тёмная: без прокрутки вбок, без рамок и полосок,
плитки ≥44 px. Снимки report-<w>-<theme>.png рядом (не коммитятся)."""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).parent; PORT=int(os.environ.get("PORT","18989")); U=f"http://127.0.0.1:{PORT}"
now=int(time.time()); TMP=Path(tempfile.gettempdir())
def c(h, area, subj, files, ago): return {"h": h, "ts": now-ago, "author": "Anna", "subject": subj, "area": area, "files": files}
REPORT={"since": now-86400, "until": now, "counts": {"site": 3, "data": 1, "server": 1, "other": 0}, "commits": [
  c("a1", "site", "feat(v2): карточка боя — строка «замена» под VS", ["web/src/fight/Card.tsx", "web/src/fight/card.css"], 600),
  c("a2", "data", "fix(odds): коэффициенты UFC 310 пересчитаны после замены соперника", ["odds/ufc.py"], 3600),
  c("a3", "site", "Значок на фото бойца, снявшегося с боя <img src=x onerror=window.__xss=1>", ["web/src/fighter/Badge.tsx"], 7200),
  c("a4", "server", "chore: ночной бэкап базы в 04:00", ["deploy/backup.sh", "deploy/cron.d/backup"], 9000),
  c("a5", "site", "style(v2): отступы списка турниров на телефоне", [], 20000)]}
rep=TMP/f"report-{PORT}.json"; rep.write_text(json.dumps(REPORT, ensure_ascii=False))
empty=TMP/f"report-empty-{PORT}.json"; empty.write_text(json.dumps({"since": now-86400, "until": now, "counts": {}, "commits": []}))
fails=0
def check(ok,name,detail=""):
    global fails; fails+=0 if ok else 1; print(("PASS" if ok else "FAIL"),name,detail,flush=True)
def serve(path):
    s=subprocess.Popen([sys.executable,str(ROOT/"stand_server.py")],env=dict(os.environ,REPORT_FILE=str(path),PORT=str(PORT),DATA_FILE=str(ROOT.parent/"stand"/"demo2.json")),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    for _ in range(50):
        try: urllib.request.urlopen(U+"/r/x.json",timeout=.3).close(); break
        except OSError: time.sleep(.1)
    return s
BORDERS="""()=>[...document.querySelectorAll('body *')].filter(e=>{const s=getComputedStyle(e);
  return ['Top','Right','Bottom','Left'].some(k=>parseFloat(s['border'+k+'Width'])>0&&s['border'+k+'Style']!=='none')}).map(e=>e.tagName+'.'+e.className)"""
srv=serve(rep)
try:
  with sync_playwright() as pw:
    for eng in ("webkit","chromium"):
      b=getattr(pw,eng).launch()
      for w,h in ((390,844),(1440,900)):
        for theme in ("light","dark"):
          ctx=b.new_context(viewport={"width":w,"height":h},color_scheme=theme,locale="ru-RU",device_scale_factor=2 if w==390 else 1)
          p=ctx.new_page(); errs=[]; p.on("pageerror",lambda e:errs.append(str(e)))
          p.goto(U+"/r/tok123"); p.wait_for_selector("section"); tag=f"{eng} {w} {theme}"
          if eng=="webkit": p.screenshot(path=str(ROOT/f"report-{w}-{theme}.png"),full_page=True)
          heads=p.locator("h2").evaluate_all("l=>l.map(e=>e.firstChild.textContent)")
          check(heads==["Сайт (UI/UX)","Прогнозы и данные","Сервер"],f"{tag} section order",str(heads))
          nums=p.locator(".sum b").evaluate_all("l=>l.map(e=>e.textContent)")
          check(nums==["3","1","1","0"],f"{tag} counts",str(nums))
          check(p.evaluate("document.documentElement.scrollWidth<=innerWidth"),f"{tag} no sideways scroll")
          check(not p.evaluate(BORDERS),f"{tag} no borders/strips",str(p.evaluate(BORDERS)[:3]))
          small=p.locator(".sum > *").evaluate_all("l=>l.filter(e=>e.getBoundingClientRect().height<44).length")
          check(small==0,f"{tag} summary tiles >=44px")
          low=p.locator("summary").evaluate_all("l=>l.filter(e=>e.getBoundingClientRect().height<44).length")
          check(low==0,f"{tag} files toggles >=44px")
          check(p.locator("#site .meta").first.inner_text().count(":")==1 and "Anna" not in p.locator("#site .meta").first.inner_text(),f"{tag} one author: time only")
          if w==390 and theme=="light":
              first=p.locator("#site li").first
              check(first.locator(".kind").inner_text()=="новое" and "Карточка боя" in first.locator(".subj").inner_text(),f"{eng} feat(v2) label, capitalized")
              check(p.evaluate("!window.__xss&&!document.querySelector('.subj img')"),f"{eng} subject not run as HTML")
              check("<img" in p.locator("#site li").nth(1).locator(".subj").inner_text(),f"{eng} subject shown as text")
              sm=p.locator("#server summary"); check(sm.inner_text()=="2 файла",f"{eng} files plural",sm.inner_text())
              sm.click(); check("deploy/backup.sh" in p.locator("#server .files").inner_text(),f"{eng} files open in place")
              check(p.locator("details").count()==4,f"{eng} no files toggle when no files")
          check(not errs,f"{tag} no page errors",str(errs[:2]))
          ctx.close()
      # устаревшая ссылка
      ctx=b.new_context(viewport={"width":390,"height":844},locale="ru-RU"); p=ctx.new_page(); p.goto(U+"/r/gone"); p.wait_for_selector(".state")
      check("Ссылка устарела" in p.inner_text(".state") and "отчёт" in p.inner_text(".state"),f"{eng} expired link text"); ctx.close()
      b.close()
  srv.terminate(); srv=serve(empty)
  with sync_playwright() as pw:
      b=pw.webkit.launch(); p=b.new_page(); p.goto(U+"/r/tok"); p.wait_for_selector(".empty")
      check("ничего не менялось" in p.inner_text(".empty") and p.locator("section").count()==0,"empty day"); b.close()
finally:
    srv.terminate()
print("TOTAL", "FAIL" if fails else "PASS"); sys.exit(1 if fails else 0)
