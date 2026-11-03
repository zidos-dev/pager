"""Моушен мини-аппа по замеру в Telegram (MOTION-BASELINE-MAC.md) и аудиту (MOTION-AUDIT.md), 19.10.
Свайп карточки: transform пишет палец/пружина — CSS-переход не должен догонять его (карточка 1:1 за пальцем).
Тост въезжает (переход из hidden стартует), а не появляется скачком.
Квота (M3): при открытии цифры стоят сразу (накат от 0 шёл, пока Telegram показывает заглушку, — срезанные цифры);
изменение квоты по-прежнему перекатывается.
Капсула фильтров (M2): пока подложка едет, выделен ровно один пункт — не два соседних.
Нажатие (вкус владельца, designkit-owner: «без нажатия-масштаба»): при зажатии масштаб 1, отклик — тоном или opacity.
Лист вопроса (designkit-owner: «лист 236–482 мс по роли, подложка 120–180 мс, без перелёта»): въезд ≤482 мс, подложка ≤180 мс.
Меню «⋯»: размытие стекла не анимируется (покадровый пересчёт blur давал 16% повторов кадров на открытии в Telegram)."""
import json, os, sys, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).parent; PORT=int(os.environ.get("PORT","18990")); U=f"http://127.0.0.1:{PORT}"
data=Path(tempfile.gettempdir())/f"motion-{PORT}.json"; data.write_text((ROOT.parent/"stand"/"demo2.json").read_text())
srv=subprocess.Popen([sys.executable,str(ROOT/"stand_server.py")],env=dict(os.environ,DATA_FILE=str(data),PORT=str(PORT)),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(U+"/api/sessions",timeout=.3).close(); break
    except OSError: time.sleep(.1)
fails=0
def check(ok,name,detail=""):
    global fails; fails+=0 if ok else 1; print(("PASS" if ok else "FAIL"),name,detail,flush=True)
SWIPE="""async()=>{const w=[...document.querySelectorAll('#list .cw')].find(e=>e.offsetHeight),c=w.querySelector('.card'),r=c.getBoundingClientRect();
 const t=(x)=>new Touch({identifier:1,target:c,clientX:x,clientY:r.top+r.height/2});
 const ev=(type,x)=>c.dispatchEvent(new TouchEvent(type,{bubbles:true,cancelable:true,touches:type==='touchend'?[]:[t(x)],changedTouches:[t(x)]}));
 const x0=r.left+r.width-30; ev('touchstart',x0); for(let i=1;i<=6;i++){ev('touchmove',x0-i*10);await new Promise(f=>requestAnimationFrame(f));}
 const m=new DOMMatrix(getComputedStyle(c).transform); const inline=c.style.transform; ev('touchend',x0-60);
 return {computedX:Math.round(m.m41),inline}}"""
try:
  with sync_playwright() as pw:
    b=pw.chromium.launch(); ctx=b.new_context(viewport={"width":390,"height":844},locale="en-US",has_touch=True,is_mobile=True)
    ctx.route("https://telegram.org/**",lambda r:r.fulfill(body=""))
    p=ctx.new_page(); errs=[]; p.on("pageerror",lambda e:errs.append(str(e))); p.on("dialog",lambda d:d.accept())
    p2=ctx.new_page(); p2.add_init_script("""new MutationObserver(()=>{const q=document.querySelector('#lab-q');if(!q||window.__m3)return;window.__m3=[];
      const f=()=>{window.__m3.push(document.querySelectorAll('#lab-q .lqs').length?[...document.querySelectorAll('#lab-q .lqs')].filter(e=>e.getAnimations().length).length:-1);if(window.__m3.length<90)requestAnimationFrame(f);};f();}).observe(document,{childList:true,subtree:true});""")
    p2.goto(U+"/app/"); p2.wait_for_timeout(2500)
    fr=p2.evaluate("window.__m3||[]")
    check(fr and max(fr)==0,"chromium quota digits stand still on open (no roll from 0, M3)",f"frames with rolling digits: {sum(1 for x in fr if x>0)}/{len(fr)}")
    D=json.loads(data.read_text()); D["sessions"]["quota"]["h5"]=min(99,D["sessions"]["quota"]["h5"]+11); data.write_text(json.dumps(D))
    p2.evaluate("()=>document.querySelector('#chips .chip').click()")
    rolled=False
    for _ in range(100):
        if p2.evaluate("()=>[...document.querySelectorAll('#lab-q .lqs')].some(e=>e.getAnimations().length)"): rolled=True; break
        p2.wait_for_timeout(100)
    check(rolled,"chromium a quota change still rolls the digits")
    p2.close()
    p.goto(U+"/app/"); p.wait_for_timeout(1500)
    m2=p.evaluate("""async()=>{const its=[...document.querySelectorAll('#lab-fcap .lf-item')],worst=[];
      for(const k of [its.length-1,0,2,its.length-2]){its[k].dispatchEvent(new MouseEvent('click',{bubbles:true}));let mx=0,mn=9;
        const t0=performance.now();while(performance.now()-t0<450){await new Promise(f=>requestAnimationFrame(f));
          const n=document.querySelectorAll('#lab-fcap .lf-item.lf-on').length;mx=Math.max(mx,n);mn=Math.min(mn,n);}
        worst.push([mn,mx]);await new Promise(f=>setTimeout(f,250));}
      return {worst,end:document.querySelectorAll('#lab-fcap .lf-item.lf-on').length}}""")
    check(all(w==[1,1] for w in m2["worst"]) and m2["end"]==1,"chromium filter capsule: one item highlighted on every frame of the slide (M2)",json.dumps(m2))
    p.goto(U+"/app/"); p.wait_for_timeout(1500)
    p.evaluate("localStorage.setItem('cc-filter','act')"); p.goto(U+"/app/"); p.wait_for_timeout(1500)  # фильтр остался от проверки M2
    p.locator('#list .card[data-id="a1c3e5f7"]').click(); p.wait_for_timeout(1600)
    sh=p.evaluate("""()=>{const s=document.querySelector('.lab-ask-sheet'),c=document.querySelector('.lab-ask-scrim');if(!s)return null;
      const d=t=>Math.round(parseFloat(t)*(/ms$/.test(t)?1:1000));return {sheet:d(getComputedStyle(s).transitionDuration.split(',')[0]),scrim:d(getComputedStyle(c).transitionDuration.split(',')[0])}}""")
    check(sh and sh["sheet"]<=482 and 0<sh["scrim"]<=180,"chromium question sheet rises ≤482 ms, scrim ≤180 ms",json.dumps(sh))
    p.goto(U+"/app/"); p.wait_for_timeout(1500)
    PRESS="""async sel=>{const e=document.querySelector(sel);if(!e)return {sel,miss:1};
      const t=e.matches('.lf-item')?e.querySelector('span.l'):e,look=()=>{const c=getComputedStyle(e),ct=getComputedStyle(t);
        return {tf:new DOMMatrix(ct.transform==='none'?undefined:ct.transform).a,sc:ct.scale==='none'?1:parseFloat(ct.scale),bg:c.backgroundColor,op:+c.opacity}};
      const a=look();return {sel,a}}"""
    for sel in ("#list .card","#new-btn","#lab-fcap .lf-item:not([aria-pressed=true])"):
        a=p.evaluate(PRESS,sel)["a"]; box=p.locator(sel).first.bounding_box()
        p.mouse.move(box["x"]+box["width"]/2,box["y"]+box["height"]/2); p.mouse.down(); p.wait_for_timeout(300)
        dn=p.evaluate(PRESS,sel)["a"]; p.mouse.move(2,2); p.mouse.up(); p.wait_for_timeout(400)
        check(abs(dn["tf"]-1)<.001 and abs(dn["sc"]-1)<.001 and (dn["bg"]!=a["bg"] or abs(dn["op"]-a["op"])>.05),
              f"chromium press on {sel}: no scale, tone/opacity feedback",json.dumps({"rest":a,"down":dn}))
    p.goto(U+"/app/"); p.wait_for_timeout(1500)
    s=p.evaluate(SWIPE)
    check(abs(s["computedX"]+60)<=2,"chromium swiped card follows the finger 1:1 (no CSS transition lag)",json.dumps(s))
    p.wait_for_timeout(600)
    op=p.evaluate("""()=>new Promise(res=>{const t=document.getElementById('toast');
      const mo=new MutationObserver(()=>{if(!t.hidden){mo.disconnect();requestAnimationFrame(()=>{const a=+getComputedStyle(t).opacity;setTimeout(()=>res([a,+getComputedStyle(t).opacity]),300);});}});
      mo.observe(t,{attributes:true,attributeFilter:['hidden']});
      const b=document.querySelector('#list .cw.open .ca button');if(!b)res('no open card');else b.click();setTimeout(()=>res('no toast'),3000);})""")
    check(isinstance(op,list) and op[0]<0.95 and op[1]>0.99,"chromium toast fades in (first frame not opaque, visible after 300 ms)",str(op))
    p.goto(U+"/app/"); p.wait_for_timeout(1500)
    p.locator('#list .card').first.click(); p.wait_for_timeout(1500)
    bl=p.evaluate("""()=>new Promise(res=>{document.getElementById('s-more').click();
      requestAnimationFrame(()=>requestAnimationFrame(()=>{const g=document.querySelector('.mn-root .mn-glass');res(g?getComputedStyle(g).backdropFilter:'no menu');}));})""")
    check("blur(40px)" in bl,"chromium menu glass is fully blurred from the first frames (blur not animated)",bl)
    p.keyboard.press("Escape"); p.wait_for_timeout(400)
    check(not errs,"chromium no page errors",str(errs[:2]))
    b.close()
finally:
    srv.terminate()
print("TOTAL", "FAIL" if fails else "PASS"); sys.exit(1 if fails else 0)
