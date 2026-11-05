/* clio.js — слои Clio для мини-аппа (перенос 1:1 со страницы выбора 15.10.2026): filter, open, ask, quota — вариант Б.
   Вариант «источник агента» не переносится (решение владельца: А).
   тема html[data-lab-theme] ставится по факту: Telegram.WebApp.colorScheme (+ themeChanged) в Telegram, иначе prefers-color-scheme.
   Тексты слоёв: i18n продукта лежит в замыкании и снаружи недоступен, поэтому слои держат свои словари ru/en
   и берут язык из document.documentElement.lang (его выставляет applyI18n продукта). */
(function(){
  var de=document.documentElement;
  var LAB=window.LAB={flags:{theme:"light"}};
  function setTheme(v){
    if(v!=="light"&&v!=="dark") return;
    if(de.getAttribute("data-lab-theme")===v) return;
    LAB.flags.theme=v; de.setAttribute("data-lab-theme",v);
    try{ window.dispatchEvent(new CustomEvent("lab:change",{detail:Object.assign({},LAB.flags,{changed:["theme"]})})); }catch(e){}
  }
  var mq=null; try{ mq=window.matchMedia("(prefers-color-scheme: dark)"); }catch(e){}
  function sysTheme(){ return mq&&mq.matches?"dark":"light"; }
  function tgTheme(){
    var w=window.Telegram&&window.Telegram.WebApp;
    if(!w||!w.initData||!de.classList.contains("in-tg")) return null;
    return w.colorScheme==="dark"?"dark":w.colorScheme==="light"?"light":null;
  }
  function sync(){ setTheme(tgTheme()||sysTheme()); }
  sync();
  if(mq){ var f=function(){ sync(); }; if(mq.addEventListener) mq.addEventListener("change",f); else if(mq.addListener) mq.addListener(f); }
  try{ var w=window.Telegram&&window.Telegram.WebApp; if(w&&w.onEvent) w.onEvent("themeChanged",sync); }catch(e){}
  /* после загрузки скриптов слоёв сообщаем стартовые флаги один раз (lab-ask подписан на lab:change) */
  window.addEventListener("load",function(){
    sync();
    window.dispatchEvent(new CustomEvent("lab:change",{detail:Object.assign({},LAB.flags,{changed:[],initial:true})}));
  });
})();

/* ===== слой filter (lab-filter.js) ===== */
/* lab-filter.js — Фильтры списка: Б — стеклянная капсула внизу с переезжающей лаймовой подложкой.
   Капсула строится из DOM #chips (MutationObserver); клик по пункту = .click() по родному чипу.
   Подложка: две независимые пружины (левый/правый край), ведущий край быстрый, хвост подтягивается — «капля».
   Состояние (положение+скорость) живёт в JS, поэтому новая цель всегда стартует от текущего положения. */
(function(){
  "use strict";
  var de=document.documentElement, $=function(id){return document.getElementById(id);};
  var cap=null, ind=null, mo=null, ro=null, on=false, rm=false;
  var raf=0, last=0, ready=false, dragging=null, selKey=null;
  var edge={L:{x:0,v:0,t:0,r:.5},R:{x:0,v:0,t:0,r:.5}}; // r = response (период пружины, с)
  var ZETA=.85;                                           // bounce .15
  var RESP_FAST=.24, RESP_SLOW=.30;                       // ведущий край / хвост: ~330 мс, без растяжения на соседний пункт (M2)
  var mq=window.matchMedia&&matchMedia("(prefers-reduced-motion: reduce)");
  function setRM(){rm=!!(mq&&mq.matches);} setRM(); if(mq&&mq.addEventListener) mq.addEventListener("change",setRM);

  /* ---------- родные чипсы: сворачивание/возврат без скачка ---------- */
  var barAnim=null;
  function bar(){return document.querySelector(".chipbar");}
  function setBar(hide,animate){
    var b=bar(); if(!b) return;
    var h0=b.getBoundingClientRect().height, cs=getComputedStyle(b);
    var pt=parseFloat(cs.paddingTop)||0, pb=parseFloat(cs.paddingBottom)||0, op=parseFloat(cs.opacity);
    if(barAnim){barAnim.onfinish=null;barAnim.cancel();barAnim=null;}
    b.classList.remove("lf-anim");
    if(hide){
      if(!animate||rm){b.classList.add("lf-gone");return;}
      if(b.classList.contains("lf-gone")) return;
      b.classList.add("lf-anim");
      barAnim=b.animate([{height:h0+"px",paddingTop:pt+"px",paddingBottom:pb+"px",opacity:op},{height:"0px",paddingTop:"0px",paddingBottom:"0px",opacity:0}],
        {duration:parseFloat(getComputedStyle(de).getPropertyValue("--m-exit-t"))||261,easing:"cubic-bezier(.4,0,.2,1)",fill:"forwards"});
      barAnim.onfinish=function(){b.classList.add("lf-gone");b.classList.remove("lf-anim");barAnim.cancel();barAnim=null;};
    }else{
      var was=b.classList.contains("lf-gone"); b.classList.remove("lf-gone");
      if(!animate||rm||!was&&h0===0) return;
      var nat=b.getBoundingClientRect().height, np=parseFloat(getComputedStyle(b).paddingTop)||0, nb=parseFloat(getComputedStyle(b).paddingBottom)||0;
      b.classList.add("lf-anim");
      barAnim=b.animate([{height:(was?0:h0)+"px",paddingTop:(was?0:pt)+"px",paddingBottom:(was?0:pb)+"px",opacity:was?0:op},{height:nat+"px",paddingTop:np+"px",paddingBottom:nb+"px",opacity:1}],
        {duration:parseFloat(getComputedStyle(de).getPropertyValue("--m-reveal-t"))||284,easing:"cubic-bezier(.25,.1,.25,1)"});
      barAnim.onfinish=function(){b.classList.remove("lf-anim");barAnim=null;};
    }
  }

  /* ---------- построение капсулы из #chips ---------- */
  function nativeChips(){var c=$("chips");return c?[].slice.call(c.querySelectorAll(".chip[data-f]")):[];}
  function items(){return cap?[].slice.call(cap.querySelectorAll(".lf-item")):[];}
  function build(){
    var ch=nativeChips(); if(!cap) return;
    var keys=ch.map(function(c){return c.dataset.f;}).join("|"), have=items().map(function(i){return i.dataset.f;}).join("|");
    if(keys!==have){
      items().forEach(function(i){i.remove();});
      ch.forEach(function(c){
        var b=document.createElement("button"); b.type="button"; b.className="lf-item"; b.dataset.f=c.dataset.f;
        b.innerHTML='<span class="l"><span class="n"></span><span class="t"></span></span>'; cap.appendChild(b);
      });
    }
    var its=items(), sel=null;
    ch.forEach(function(c,i){
      var b=its[i], n=c.querySelector(".n"), txt=c.firstChild&&c.firstChild.nodeType===3?c.firstChild.nodeValue:c.textContent;
      var tEl=b.querySelector(".t"), nEl=b.querySelector(".n");
      if(tEl.textContent!==txt) tEl.textContent=txt;
      var nv=n?n.textContent:""; if(nEl.textContent!==nv) nEl.textContent=nv;
      var p=c.getAttribute("aria-pressed")==="true"; b.setAttribute("aria-pressed",p?"true":"false"); if(p) sel=b;
    });
    cap.classList.toggle("lf-empty",!its.length);
    if(sel) selKey=sel.dataset.f;
    retarget(false);
  }
  var pend=false;
  function schedule(){if(pend)return;pend=true;requestAnimationFrame(function(){pend=false;if(on)build();});}

  /* ---------- пружина подложки ---------- */
  function selItem(){var s=null;items().forEach(function(i){if(i.getAttribute("aria-pressed")==="true")s=i;});return s;}
  function paint(){
    var w=Math.max(0,edge.R.x-edge.L.x);
    ind.style.translate=edge.L.x.toFixed(2)+"px 0"; ind.style.width=w.toFixed(2)+"px";
    // цвет текста — у одного пункта, ближайшего к центру подложки: растянутая пружиной подложка накрывает два (M2)
    var its=items(), i, c, d, best=-1, bd=1e9, mid=(edge.L.x+edge.R.x)/2;
    for(i=0;i<its.length;i++){c=its[i].offsetLeft+its[i].offsetWidth/2;d=Math.abs(c-mid);if(d<bd){bd=d;best=i;}}
    for(i=0;i<its.length;i++){if(its[i].classList.contains("lf-on")!==(i===best))its[i].classList.toggle("lf-on",i===best);}
  }
  function retarget(animate){
    if(!cap||dragging) return;
    var s=selItem(); if(!s){ind.style.opacity="0";return;}
    var l=s.offsetLeft, r=l+s.offsetWidth;
    if(!cap.offsetWidth) return;                       // скрыта/без раскладки
    ind.style.opacity="1";
    if(!ready||rm){
      if(rm&&ready&&(Math.abs(edge.L.x-l)>.5||Math.abs(edge.R.x-r)>.5)) ind.animate([{opacity:0},{opacity:1}],{duration:120,easing:"linear"});
      edge.L.x=edge.L.t=l; edge.R.x=edge.R.t=r; edge.L.v=edge.R.v=0; paint(); ready=true; return;
    }
    if(l===edge.L.t&&r===edge.R.t) return;
    var right=(l+r)/2>(edge.L.t+edge.R.t)/2;
    // ведущий край — быстрый, хвост — медленнее
    edge.R.r=right?RESP_FAST:RESP_SLOW; edge.L.r=right?RESP_SLOW:RESP_FAST;
    edge.L.t=l; edge.R.t=r;
    if(!raf){last=performance.now();raf=requestAnimationFrame(step);}
  }
  function stepEdge(e,dt){
    var w=2*Math.PI/e.r, a=w*w*(e.t-e.x)-2*ZETA*w*e.v;
    e.v+=a*dt; e.x+=e.v*dt;
  }
  function step(now){
    var dt=Math.min(.05,(now-last)/1000); last=now;
    var n=Math.max(1,Math.ceil(dt/.004)), h=dt/n;
    for(var i=0;i<n;i++){stepEdge(edge.L,h);stepEdge(edge.R,h);}
    paint();
    var done=Math.abs(edge.L.x-edge.L.t)<.15&&Math.abs(edge.R.x-edge.R.t)<.15&&Math.abs(edge.L.v)<2&&Math.abs(edge.R.v)<2;
    if(done){edge.L.x=edge.L.t;edge.R.x=edge.R.t;edge.L.v=edge.R.v=0;paint();raf=0;return;}
    raf=requestAnimationFrame(step);
  }

  /* ---------- выбор пункта ---------- */
  function choose(b,silent){
    if(!b) return;
    var native=nativeChips().filter(function(c){return c.dataset.f===b.dataset.f;})[0];
    items().forEach(function(i){i.setAttribute("aria-pressed",i===b?"true":"false");});
    selKey=b.dataset.f;
    retarget(true);
    if(native&&native.getAttribute("aria-pressed")!=="true"){
      native.click();
      var L=$("list"); if(L&&!rm&&L.animate) L.animate([{opacity:.4},{opacity:1}],{duration:200,easing:"cubic-bezier(.2,.8,.2,1)"});  // M2: не скачок за кадр
    }
  }

  /* ---------- указатель: нажатие, свайп с проекцией ---------- */
  var tr=[];
  function onDown(e){
    if(e.button!==undefined&&e.button!==0) return;
    var it=e.target.closest(".lf-item"); if(!it) return;
    dragging={id:e.pointerId,x0:e.clientX,item:it,moved:false,l0:edge.L.x,r0:edge.R.x};
    tr=[[performance.now(),e.clientX]];
    it.classList.add("lf-press");
    if(it.getAttribute("aria-pressed")==="true") cap.classList.add("lf-hold");
    try{cap.setPointerCapture(e.pointerId);}catch(x){}
  }
  function onMove(e){
    var d=dragging; if(!d||e.pointerId!==d.id) return;
    var dx=e.clientX-d.x0, t=performance.now();
    tr.push([t,e.clientX]); while(tr.length>2&&t-tr[0][0]>100) tr.shift();
    if(!d.moved){
      if(Math.abs(dx)<6) return;
      d.moved=true; cap.classList.add("lf-drag"); cap.classList.remove("lf-hold");
      d.item.classList.remove("lf-press");
      if(raf){cancelAnimationFrame(raf);raf=0;}
      d.l0=edge.L.x; d.r0=edge.R.x; d.x0=e.clientX-0; dx=0; edge.L.v=edge.R.v=0;
    }
    var its=items(), min=its[0].offsetLeft, max=its[its.length-1].offsetLeft+its[its.length-1].offsetWidth, w=d.r0-d.l0;
    var l=Math.max(min,Math.min(max-w,d.l0+dx));
    edge.L.x=edge.L.t=l; edge.R.x=edge.R.t=l+w; paint();      // 1:1 за пальцем
  }
  function vel(){if(tr.length<2)return 0;var a=tr[0],b=tr[tr.length-1],dt=(b[0]-a[0])/1000;return dt>0?(b[1]-a[1])/dt:0;}
  function onUp(e){
    var d=dragging; if(!d||e.pointerId!==d.id) return;
    dragging=null; cap.classList.remove("lf-drag","lf-hold"); d.item.classList.remove("lf-press");
    try{cap.releasePointerCapture(e.pointerId);}catch(x){}
    if(e.type==="pointercancel"){retarget(true);return;}
    if(!d.moved){choose(d.item);return;}
    var v=vel(), c=(edge.L.x+edge.R.x)/2+v*0.099;               // проекция инерции (d=.99)
    var best=null,bd=1e9;
    items().forEach(function(i){var dd=Math.abs(i.offsetLeft+i.offsetWidth/2-c);if(dd<bd){bd=dd;best=i;}});
    // подложка стартует с текущего положения и скорости пальца
    edge.L.v=edge.R.v=v; edge.L.t=edge.L.x; edge.R.t=edge.R.x;
    choose(best);
    if(!raf){last=performance.now();raf=requestAnimationFrame(step);}
  }
  function onCancelPress(){}
  function onKey(e){ // клавиатура: click без указателя
    var it=e.target.closest(".lf-item"); if(it&&e.detail===0) choose(it);
  }

  /* ---------- видимость капсулы по экрану ---------- */
  function syncAway(){
    if(!cap) return;
    var list=$("list-screen"), away=de.classList.contains("locked")||!list||list.hidden||(function(){var g=$("gate-screen");return g&&!g.hidden;})();
    cap.classList.toggle("lf-away",!!away);
    if(away&&dragging){dragging=null;cap.classList.remove("lf-drag","lf-hold");}
    if(!away) retarget(false);
  }

  /* ---------- включение / выключение ---------- */
  function enable(initial){
    if(on) return; on=true;
    cap=document.createElement("nav"); cap.id="lab-fcap"; cap.setAttribute("aria-label","Filter");
    ind=document.createElement("span"); ind.className="lf-ind"; ind.setAttribute("aria-hidden","true"); cap.appendChild(ind);
    document.body.appendChild(cap);
    cap.addEventListener("pointerdown",onDown); cap.addEventListener("pointermove",onMove);
    cap.addEventListener("pointerup",onUp); cap.addEventListener("pointercancel",onUp);
    cap.addEventListener("click",onKey);
    ready=false; edge.L.x=edge.L.t=edge.R.x=edge.R.t=0; edge.L.v=edge.R.v=0;
    var c=$("chips");
    mo=new MutationObserver(schedule);
    if(c) mo.observe(c,{childList:true,subtree:true,attributes:true,characterData:true});
    if(window.ResizeObserver){ro=new ResizeObserver(function(){if(on)retarget(false);});ro.observe(cap);}
    setBar(true,!initial);
    build(); syncAway();
    // вход снизу при переключении на лету; при загрузке — без выезда
    if(!initial){cap.classList.add("lf-away");void cap.offsetWidth;syncAway();}
    var stateMo=new MutationObserver(syncAway); stateMo.observe(de,{attributes:true,attributeFilter:["class"]});
    ["list-screen","gate-screen"].forEach(function(id){var el=$(id);if(el)stateMo.observe(el,{attributes:true,attributeFilter:["hidden","data-perm-busy"]});});
    cap._stateMo=stateMo;
  }
  enable(document.readyState!=="complete");
  function boot(){ if(!on) enable(true); }
  if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",boot); else boot();
})();

/* ===== слой open (lab-open.js) ===== */
/* lab-open.js — Открытие сессии: Б — раскрытие карточки на месте поверх размытого списка.
   Продукт целиком в IIFE (push/pop/view приватны), поэтому хук — через DOM:
   - capture-клик по #list .card[data-id] запоминает прямоугольник карточки; открытие продукт делает сам (данные, render);
   - MutationObserver на #session-screen[hidden] видит появление экрана и запускает FLIP: --lo-p 0→1 через WAAPI;
     слайд продукта для сессии не запускается: экран открывает и закрывает только этот морф (session:closed — конец свёртки);
   - назад: session:close после закрытия оверлеев запускает свёртку от ТЕКУЩЕГО --lo-p (прерываемо);
     свайп от края: продукт шлёт nav:drag {x}, ведём --lo-p = 1 - x/W (1:1 за пальцем); отпускание — session:close или nav:cancel;
   - когда продукт скрывает экран (hidden), всё чистится. */
(function(){
  var de=document.documentElement, scr=document.getElementById("session-screen");
  if(!scr){ console.warn("lab-open: no session screen"); return; }
  var pending=null, stamp=0, anim=null, phase="", ghost=null, dim=null, dragging=false, lastHidden=scr.hidden, closeTimer=0;

  function on(){ return !matchMedia("(min-width:1000px)").matches; }
  function ms(n){ var v=parseFloat(getComputedStyle(de).getPropertyValue(n)); return isFinite(v)?v:592; }
  function curve(){ return getComputedStyle(de).getPropertyValue("--m-expand").trim()||"ease-out"; }
  function curP(){ var v=parseFloat(getComputedStyle(de).getPropertyValue("--lo-p")); return isFinite(v)?v:1; }
  function setP(p){ de.style.setProperty("--lo-p",String(p)); }
  function stopAnim(){ if(anim){ var p=curP(); try{anim.cancel();}catch(e){} anim=null; setP(p); } }
  /* скругление экрана (--lo-rr): 1 на всём движении; 1→0 только после полного раскрытия (затем lo-flat снимает clip-path),
     0→1 в самом начале свёртки/свайпа — скачка радиуса нет */
  var rrAnim=null;
  function curRR(){ var v=parseFloat(getComputedStyle(de).getPropertyValue("--lo-rr")); return isFinite(v)?v:1; }
  function setRR(v){ de.style.setProperty("--lo-rr",String(v)); }
  function stopRR(){ if(rrAnim){ var v=curRR(); try{rrAnim.cancel();}catch(e){} rrAnim=null; setRR(v); } }
  function runRR(to,dur,done){
    stopRR(); var from=curRR();
    if(Math.abs(to-from)<0.002){ setRR(to); done&&done(); return; }
    var a=de.animate([{"--lo-rr":String(from)},{"--lo-rr":String(to)}],{duration:dur,easing:"cubic-bezier(.25,.1,.25,1)",fill:"none"});
    rrAnim=a;
    a.onfinish=function(){ if(rrAnim!==a) return; rrAnim=null; setRR(to); done&&done(); };
  }
  function run(to,full,done){
    stopAnim();
    var from=curP(), dist=Math.abs(to-from);
    if(dist<0.002){ setP(to); done&&done(); return; }
    var a=de.animate([{"--lo-p":String(from)},{"--lo-p":String(to)}],{duration:Math.max(90,full*Math.max(.3,dist)),easing:curve(),fill:"none"});
    anim=a;
    a.onfinish=function(){ if(anim!==a) return; anim=null; setP(to); done&&done(); };
  }
  function ensureDim(){ if(!dim||!dim.isConnected){ dim=document.createElement("div"); dim.id="lo-dim"; document.body.appendChild(dim); } }
  function killGhost(){ if(ghost){ ghost.remove(); ghost=null; } }
  function listEl(){ return document.getElementById("list-screen"); }
  function listScale(){ return 1-0.06*curP(); }

  /* прямоугольник карточки в нормальном (неумасштабированном) состоянии списка */
  function cardRect(el,scale){
    if(!el||!el.isConnected) return null;
    var r=el.getBoundingClientRect(), W=window.innerWidth, H=window.innerHeight;
    if(scale!==1){
      var ox=W/2, oy=(parseFloat(de.style.getPropertyValue("--lo-oy"))||H/2)-window.scrollY;
      var x1=ox+(r.left-ox)/scale, x2=ox+(r.right-ox)/scale, y1=oy+(r.top-oy)/scale, y2=oy+(r.bottom-oy)/scale;
      r={left:x1,right:x2,top:y1,bottom:y2,width:x2-x1,height:y2-y1};
    }
    if(r.bottom<0||r.top>H||r.width<8) return null;
    return r;
  }
  function applyRect(el,r){
    var W=window.innerWidth, H=scr.getBoundingClientRect().height||window.innerHeight, rad=14;
    if(r&&el){ rad=parseFloat(getComputedStyle(el).borderTopLeftRadius)||14; }
    else{ r={left:W*.3,right:W*.7,top:H*.36,bottom:H*.64,width:0}; rad=28; el=null; }
    de.style.setProperty("--lo-t",Math.max(-40,r.top).toFixed(1)+"px");
    de.style.setProperty("--lo-l",r.left.toFixed(1)+"px");
    de.style.setProperty("--lo-r",(W-r.right).toFixed(1)+"px");
    de.style.setProperty("--lo-b",Math.max(-40,H-r.bottom).toFixed(1)+"px");
    de.style.setProperty("--lo-rad",rad+"px");
    killGhost();
    if(el){
      ghost=document.createElement("div"); ghost.className="lo-ghost";
      var c=el.cloneNode(true); c.removeAttribute("id"); c.removeAttribute("data-id");
      ghost.style.cssText="left:"+r.left+"px;top:"+r.top+"px;width:"+r.width+"px;height:"+r.height+"px";
      ghost.setAttribute("aria-hidden","true"); ghost.appendChild(c); document.body.appendChild(ghost);
    }
  }
  function clean(){
    clearTimeout(closeTimer); stopAnim(); stopRR(); killGhost(); phase=""; dragging=false;
    de.classList.remove("lo-on","lo-rest","lo-flat");
    ["--lo-rr","--lo-p","--lo-t","--lo-l","--lo-r","--lo-b","--lo-rad","--lo-oy"].forEach(function(k){de.style.removeProperty(k);});
  }
  function cardById(id){
    if(id==null) return null;
    try{ return document.querySelector('#list .card[data-id="'+(window.CSS&&CSS.escape?CSS.escape(id):id)+'"]'); }catch(e){ return null; }
  }
  /* свёртка (кнопка или свайп): целевая карточка берётся из DOM прямо сейчас; нет карточки — сжатие к центру */
  function prepClose(){
    de.classList.remove("lo-rest","lo-flat"); de.classList.add("lo-on"); ensureDim();
    runRR(1,120);
    var el=cardById(scr.dataset.loId);
    applyRect(el,cardRect(el,listScale()));
  }

  function onShown(){
    if(!on()) return;
    var p=pending&&Date.now()-stamp<900?pending:null; pending=null;
    clean(); ensureDim();
    de.style.setProperty("--lo-oy",(window.scrollY+window.innerHeight/2)+"px");
    de.style.setProperty("--lo-p","0");
    de.classList.add("lo-on"); phase="opening";
    scr.dataset.loId=p?p.id:"";
    var el=p&&p.el&&p.el.isConnected?p.el:null;
    applyRect(el,p?p.rect:null);
    setRR(1);
    run(1,ms("--m-expand-t"),settleOpen);
  }
  function onHidden(){ if(de.classList.contains("lo-on")||phase) clean(); }
  function closed(){ document.dispatchEvent(new Event("session:closed")); }
  function startClose(){
    if(!on()||scr.hidden||phase==="closing"){ if(!on()||scr.hidden) closed(); return; }
    phase="closing";
    if(dragging) dragging=false; else{ stopAnim(); prepClose(); }  // после свайпа сворачиваем от текущего --lo-p
    run(0,ms("--m-expand-out-t"),closed);  // продукт скрывает экран по session:closed — без паузы в состоянии «карточка»
  }
  function settleOpen(){ phase="open"; de.classList.add("lo-rest"); killGhost(); runRR(0,220,function(){ if(phase==="open") de.classList.add("lo-flat"); }); }
  /* свайп от края: продукт шлёт nav:drag {x} (1:1 за пальцем) и nav:cancel при отпускании без закрытия; закрытие — session:close */
  document.addEventListener("nav:drag",function(e){
    if(!on()||scr.hidden||phase==="closing"||phase==="opening") return;
    var x=+e.detail||0, W=window.innerWidth;
    if(!dragging){ if(x<=0.5) return; dragging=true; stopAnim(); prepClose(); }
    setP(Math.max(0,Math.min(1,1-x/W)));
  });
  document.addEventListener("nav:cancel",function(){
    if(!dragging) return;
    dragging=false; phase="opening";
    run(1,ms("--m-expand-t"),settleOpen);
  });
  new MutationObserver(function(){
    var h=scr.hidden;
    if(h!==lastHidden){ lastHidden=h; if(h) onHidden(); else onShown(); }
  }).observe(scr,{attributes:true,attributeFilter:["hidden"]});

  document.addEventListener("click",function(e){
    if(!on()) return;
    var c=e.target.closest&&e.target.closest("#list .card[data-id]");
    if(!c||(scr.hidden===false&&phase!=="closing")) return;
    var w=c.closest(".cw"); if(w&&(w.dataset.swiped||w.classList.contains("open"))) return;
    pending={id:c.dataset.id,el:c,rect:cardRect(c,1)}; stamp=Date.now();
    if(!scr.hidden) setTimeout(onShown,0); /* экран переиспользован посреди свёртки: hidden не меняется */
    setTimeout(function(){ pending=null; },300);
  },true);
  document.addEventListener("session:close",startClose);

})();

/* ===== слой ask (lab-ask.js) ===== */
/* lab-ask.js — Вопрос агента, вариант Б: стеклянный лист снизу (Clio: листы, стекло, слои).
   Родной блок #s-question скрывается классом html.lab-ask-has
   (display:none; его кнопки остаются кликабельными программно: действие всегда делает продукт).
   Лист строится из DOM родного блока (.q, .opt[data-i], кнопки data-a="own|cancel|next").
   Детенты: open (y=0) и pill (y=hiddenY); перетаскивание 1:1, сверху резина, отпускание — по проекции скорости.
   Выбор: подсветка лаймом -> клик по родному .opt -> лист опускается, текст летит (FLIP) в исходящий пузырь
   (.me.pending в #s-hist: продукт сам удаляет .pending при следующей отрисовке истории). */
(function(){
  var D=document, de=D.documentElement;
  var $=function(id){return D.getElementById(id);};
  var S={root:null,sheet:null,scrim:null,pill:null,sig:null,dismissed:null,state:"open",y:0,marks:[],busy:false,showT:0,drag:null,justDragged:false,opts:[],multi:false,own:false,ownAnimation:null};
  var reduce=function(){try{return matchMedia("(prefers-reduced-motion: reduce)").matches;}catch(e){return false;}};
  var ru=function(){var l=de.lang||"";if(!l)try{l=navigator.language||"";}catch(e){}return /^ru/i.test(l);};
  var tok=function(n){return getComputedStyle(de).getPropertyValue(n).trim();};
  var ms=function(n,d){var v=parseFloat(tok(n));return isFinite(v)?v:d;};
  var clamp=function(v,a,b){return Math.max(a,Math.min(b,v));};
  var esc=function(s){return String(s).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});};
  var L=function(){return ru()?{cap:"Вопрос агента",pill:function(n){var m=n%10,h=n%100;return "Вопрос агента · "+n+(m===1&&h!==11?" вариант":(m>=2&&m<=4&&(h<12||h>14)?" варианта":" вариантов"));},done:"Готово"}
      :{cap:"Agent question",pill:function(n){return "Agent question · "+n+(n===1?" option":" options");},done:"Done"};};

  /* ---------- чтение родного блока ---------- */
  function parse(){
    var el=$("s-question"); if(!el||el.hidden) return null;
    var card=el.querySelector(".perm-card");
    if(card){
      var p=JSON.parse(card.dataset.perm);
      return {permission:true,q:p.title,cap:card.dataset.cap,pill:card.dataset.pill,sid:card.dataset.session,prompt:p,multi:false,
        opts:Array.prototype.map.call(card.querySelectorAll(".opt"),function(b){return {i:+b.dataset.i,label:b.dataset.label,short:b.querySelector(".perm-label").textContent,deny:b.classList.contains("perm-deny"),selected:b.classList.contains("perm-selected")};})};
    }
    var sb=el.querySelector('[data-a="submit"]');
    if(sb){ /* шаг «Ready to submit your answers?» многовопросного AskUserQuestion: родной блок вверху ленты не виден — лист показывает ответы и «Отправить» */
      var cb=el.querySelector('[data-a="cancel"]');
      return {review:true,sid:el.dataset.session,key:el.dataset.question,labels:JSON.parse(el.dataset.ownLabels),pending:false,ownState:null,multi:false,opts:[],
        q:(el.querySelector("h3")||{}).textContent||"",answers:Array.prototype.map.call(el.querySelectorAll(".reason"),function(d){return d.textContent;}),
        submit:sb.textContent.trim(),skip:cb?cb.textContent.trim():(ru()?"Отмена":"Cancel")};
    }
    var os=el.querySelectorAll(".opt[data-i]"); if(!os.length) return null;
    var q={sid:el.dataset.session,key:el.dataset.question,labels:JSON.parse(el.dataset.ownLabels),pending:el.dataset.ownBusy==="true",ownState:JSON.parse(el.dataset.ownState||"null"),q:(el.querySelector(".q")||{}).textContent||"",step:(el.querySelector(".qstep")||{}).textContent||"",opts:[],multi:el.dataset.multi==="true",more:el.dataset.more==="true"};
    os.forEach(function(b){
      var sp=b.querySelectorAll("span"), lab=sp[sp.length-1], sm=b.querySelector("small");
      q.opts.push({i:+b.dataset.i,label:(lab&&lab.childNodes[0]?lab.childNodes[0].textContent:b.textContent).trim(),desc:sm?sm.textContent.trim():"",marked:b.getAttribute("aria-pressed")==="true"});
    });
    var btn=function(a){var b=el.querySelector('[data-a="'+a+'"]');return b?b.textContent.trim():null;};
    q.own=btn("own")||(ru()?"Свой ответ":"Your answer"); q.skip=btn("cancel")||(ru()?"Пропустить":"Skip"); q.next=btn("next");
    return q;
  }
  function nat(sel){return $("s-question")&&$("s-question").querySelector(sel);}
  var sigOf=function(q){if(q.permission) return JSON.stringify([q.sid,q.cap,q.prompt]); return JSON.stringify([q.sid,q.key]);};

  /* ---------- построение ---------- */
  function build(q){
    /* следующий шаг многовопросного вопроса: лист остаётся поднятым, меняется содержимое и высота (без ухода и въезда) */
    var stepFrom=S.root&&!q.permission&&!S.permission&&S.sid===q.sid&&S.root.dataset.state==="open"?S.sheet.getBoundingClientRect().height:0;
    teardown(true);
    var root=D.createElement("div"); root.className="lab-ask-root"+(q.permission?" permission":""); root.dataset.state="open";
    var rows=q.opts.map(function(o,k){
      return '<button type="button" class="lab-ask-opt'+(o.deny?" perm-deny":"")+(o.selected?" perm-selected":"")+'"'+(q.permission?' title="'+esc(o.label)+'" aria-label="'+esc(o.label)+'"':'')+' data-k="'+k+'" style="--i:'+k+'" aria-pressed="'+(q.multi&&o.marked)+'">'
        +'<span class="lab-ask-mk">'+(q.multi?'<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.2 8.4l3 3 6.6-6.8" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>':(q.permission&&q.prompt.kind!=="sdk"?o.i:k+1))+'</span>'
        +'<span class="lab-ask-tx"><span class="lab-ask-t">'+esc(q.permission?o.short:o.label)+'</span>'+(o.desc?'<span class="lab-ask-d">'+esc(o.desc)+'</span>':'')+'</span></button>';
    }).join("");
    if(q.review) rows=q.answers.map(function(a){return '<div class="lab-ask-ans">'+esc(a)+'</div>';}).join("");
    var cap=q.permission?q.cap:L().cap, pillText=q.permission?q.pill:(q.review?q.q:L().pill(q.opts.length));
    var details=q.permission?["summary","path","body","note","question"].map(function(k){return q.prompt[k]?'<'+(k==="body"?"pre":"div")+' class="lab-perm-'+k+'">'+esc(q.prompt[k])+'</'+(k==="body"?"pre":"div")+'>':"";}).join(""):"";
    root.innerHTML='<div class="lab-ask-scrim"></div>'
      +'<section class="lab-ask-sheet" role="dialog" aria-label="'+esc(cap)+'">'
      +'<div class="lab-ask-head"><div class="lab-ask-grip"></div><div class="lab-ask-cap">'+esc(cap)+'</div><div class="lab-ask-q">'+esc(q.q)+'</div></div>'
      +(q.permission?'<div class="lab-perm-content">'+details:'')+'<div class="lab-ask-list">'+rows+'</div>'+(q.permission?'</div>':'')
      +(q.review?'<div class="lab-ask-actions"><button type="button" class="lab-ask-btn pri" data-act="submit">'+esc(q.submit)+'</button><button type="button" class="lab-ask-btn" data-act="cancel">'+esc(q.skip)+'</button></div>':'')
      +(q.permission||q.review?'':'<div class="lab-ask-actions">'+(q.multi?'<button type="button" class="lab-ask-btn pri" data-act="next">'+esc(q.next||L().done)+'</button>':'')
      +'<button type="button" class="lab-ask-btn" data-act="own">'+esc(q.own)+'</button><button type="button" class="lab-ask-btn" data-act="cancel">'+esc(q.skip)+'</button></div>'
      +'<form class="lab-ask-own" hidden><label for="lab-ask-own-text">'+esc(q.own)+'</label>'
      +'<textarea id="lab-ask-own-text" class="lab-ask-own-text" rows="2" aria-describedby="lab-ask-own-error"></textarea>'
      +'<div id="lab-ask-own-error" class="lab-ask-own-error" role="alert" hidden></div>'
      +'<div class="lab-ask-own-actions"><button type="submit" class="lab-ask-btn pri" data-act="own-send" disabled>'+esc(q.labels.send)+'</button>'
      +'<button type="button" class="lab-ask-btn" data-act="own-cancel">'+esc(q.labels.cancel)+'</button></div></form>')+'</section>'
      +'<button type="button" class="lab-ask-pill" aria-label="'+esc(pillText)+'"><span class="lab-ask-pdot"></span><span class="lab-ask-ptx">'+esc(pillText)+'</span>'
      +'<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M3.5 10l4.5-4.5L12.5 10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></button>';
    D.body.appendChild(root);
    S.root=root; S.sheet=root.querySelector(".lab-ask-sheet"); S.scrim=root.querySelector(".lab-ask-scrim"); S.pill=root.querySelector(".lab-ask-pill");
    S.sig=sigOf(q); S.sid=q.sid; S.key=q.key; S.labels=q.labels; S.ownPending=false; S.opts=q.opts; S.multi=q.multi; S.more=!!q.more; S.permission=!!q.permission; S.marks=q.opts.map(function(o){return !!o.marked;}); S.busy=false; S.own=false; S.state="open";
    var list=root.querySelector(".lab-ask-list");
    root.classList.toggle("multi",q.multi);
    bind(root);
    if(q.permission) permColor(root);
    if(stepFrom){
      root.classList.add("step"); go("open",false);
      if(!reduce()&&S.sheet.animate){
        var to=S.sheet.getBoundingClientRect().height;
        S.sheet.animate([{height:stepFrom+"px"},{height:to+"px"}],{duration:ms("--m-state-t",302),easing:tok("--m-state")||"ease"});
        root.querySelectorAll(".lab-ask-q,.lab-ask-list,.lab-ask-actions").forEach(function(el){ el.animate([{opacity:0},{opacity:1}],{duration:180,easing:tok("--m-fade")||"ease",fill:"backwards"}); });
      }
      requestAnimationFrame(function(){ list.classList.toggle("scrolls",list.scrollHeight>list.clientHeight+1); root.classList.toggle("scrolls",list.scrollHeight>list.clientHeight+1); });
      return;
    }
    /* старт: лист за краем, затем въезд */
    S.sheet.style.transition="none"; S.sheet.style.transform="translate3d(0,"+hiddenY()+"px,0)"; S.y=hiddenY();
    paintProgress(1,false);
    void S.sheet.offsetHeight;
    clearTimeout(S.showT);
    S.showT=setTimeout(function(){ if(S.root===root) go("open",true); }, reduce()?0:140);
    requestAnimationFrame(function(){ list.classList.toggle("scrolls",list.scrollHeight>list.clientHeight+1); root.classList.toggle("scrolls",list.scrollHeight>list.clientHeight+1); });
  }
  function permColor(root){
    var row=root.querySelector(".perm-deny"); if(!row) return;
    var raw=tok("--tg-theme-destructive-text-color");
    if(!raw) return;
    row.style.color=raw;
    function rgb(v){return (v.match(/[\d.]+/g)||[]).slice(0,3).map(Number);}
    function lum(v){return v.reduce(function(a,x,i){x/=255;return a+[.2126,.7152,.0722][i]*(x<=.04045?x/12.92:Math.pow((x+.055)/1.055,2.4));},0);}
    var fg=rgb(getComputedStyle(row).color), bg=rgb(getComputedStyle(row).backgroundColor);
    var a=lum(fg), b=lum(bg);
    if(fg.length!==3||bg.length!==3||(Math.max(a,b)+.05)/(Math.min(a,b)+.05)<4.5) row.style.color="";
  }
  function teardown(now){
    clearTimeout(S.showT);
    if(S.ownAnimation){ S.ownAnimation.cancel(); S.ownAnimation=null; }
    var body=$("s-body"); if(body) body.style.removeProperty("--ask-space");
    if(pillRO) pillRO.disconnect();
    var r=S.root; if(!r) return;
    if(r.cancelHold) r.cancelHold();
    S.root=S.sheet=S.scrim=S.pill=null; S.sig=null; S.drag=null;
    if(now){ r.remove(); return; }
    r.classList.add("leaving"); setTimeout(function(){ r.remove(); },420);
  }

  /* ---------- положение и детенты ---------- */
  function hiddenY(){ return (S.sheet?S.sheet.offsetHeight:420)+40; }
  function curY(){ if(!S.sheet) return 0; try{ return new DOMMatrix(getComputedStyle(S.sheet).transform).m42; }catch(e){ return S.y; } }
  function paintProgress(p,anim){
    /* p: 0 лист открыт, 1 лист убран. Лента затемняется/размывается только пока лист открыт; пилюля проявляется к концу. */
    if(!S.root) return;
    var t=anim?"opacity "+(reduce()?120:180)+"ms "+tok("--m-fade"):"none";  // подложка 120–180 мс (designkit-owner)
    S.scrim.style.transition=t; S.scrim.style.opacity=String(clamp(1-p*1.15,0,1));
    var po=clamp((p-.55)/.45,0,1);
    S.pill.style.transition=anim?"opacity 240ms "+tok("--m-fade")+", transform "+(reduce()?120:ms("--m-state-t",302))+"ms "+tok("--m-state"):"none";
    S.pill.style.opacity=String(po); S.pill.style.transform="translate3d(0,"+(1-po)*14+"px,0)";
  }
  function go(state,anim,quick){
    if(!S.sheet) return;
    clearTimeout(S.showT);
    S.state=state; S.root.dataset.state=state;
    var y=state==="open"?0:hiddenY(); S.y=y;
    var dur=reduce()?120:(state==="open"?ms("--m-sheet-t",482):(quick?ms("--m-sheet-out-t",355):ms("--m-sheet-out-t",355)));
    S.sheet.style.transition=anim?"transform "+dur+"ms "+(reduce()?"linear":tok("--m-sheet")):"none";
    S.sheet.style.transform="translate3d(0,"+y+"px,0)";
    paintProgress(state==="open"?0:1,anim);
    if(state==="pill") try{ D.activeElement&&S.sheet.contains(D.activeElement)&&D.activeElement.blur(); }catch(e){}
    placePill();
  }
  function placePill(){
    if(!S.pill) return;
    var f=D.querySelector(".sfoot"), b=18, bottom=S.root.getBoundingClientRect().bottom;
    if(f&&f.offsetParent!==null){ var r=f.getBoundingClientRect(); b=Math.max(18,bottom-r.top+10); }
    S.pill.style.bottom=b+"px";
    var body=$("s-body");
    if(body){
      /* запас учитывает высоту пилюли и зазор над полем ввода */
      var h=S.pill.offsetHeight, gap=Math.max(0,body.getBoundingClientRect().bottom-(bottom-b));
      if(S.state==="pill"&&!S.busy) body.style.setProperty("--ask-space",(h+gap+12)+"px");
      else body.style.removeProperty("--ask-space");
    }
  }
  var rub=function(d){return 64*(1-Math.exp(-d/128));};

  /* ---------- жесты ---------- */
  function bind(root){
    var sheet=S.sheet, pill=S.pill, hold=0, held=false;
    function cancelHold(){ clearTimeout(hold); }
    root.cancelHold=cancelHold;
    sheet.addEventListener("pointerdown",function(e){
      var row=e.target.closest(".lab-ask-opt"); if(!S.permission||!row||S.busy) return;
      held=false; cancelHold();
      hold=setTimeout(function(){ held=true; var o=S.opts[+row.dataset.k]; if(o) $("s-question").dispatchEvent(new CustomEvent("perm:hint",{detail:o.label})); },500);
    });
    ["pointerup","pointercancel","pointermove","pointerleave"].forEach(function(ev){sheet.addEventListener(ev,cancelHold);});
    /* Завершаем тап на затемнении до смены pointer-events: иначе click попадёт в шапку под ним.
       Обратно: тап по пилюле раскрывает лист на pointerup, и перенацеленный click ловит уже оживший scrim —
       гасим по justDragged, тому же флагу, что у листа и пилюли. */
    root.querySelector(".lab-ask-scrim").addEventListener("click",function(){ if(S.justDragged) return; if(S.state==="open"&&!S.busy) go("pill",true); });
    pill.addEventListener("pointerdown",function(e){ dragStart(e,pill,true); });
    sheet.addEventListener("pointerdown",function(e){
      var inList=e.target.closest(".lab-ask-list"); var list=sheet.querySelector(".lab-ask-list");
      var row=e.target.closest(".lab-ask-opt,.lab-ask-btn"); if(row&&!e.target.closest(".lab-ask-btn")) press(row,true);
      if(e.target.closest(".lab-perm-content,.lab-ask-own")) return;
      if(inList&&root.classList.contains("scrolls")&&e.pointerType!=="mouse") return;
      dragStart(e,sheet,false);
    });
    sheet.addEventListener("click",function(e){
      if(held){ held=false; e.stopPropagation(); e.preventDefault(); return; }
      if(S.justDragged){ e.stopPropagation(); e.preventDefault(); return; }
      var o=e.target.closest(".lab-ask-opt"); if(o){ choose(+o.dataset.k); return; }
      var b=e.target.closest(".lab-ask-btn"); if(!b||S.busy) return;
      var a=b.dataset.act;
      if(a==="own"){ ownEditor(true); }
      else if(a==="own-cancel"){ ownEditor(false); }
      else if(a==="own-send"){ e.preventDefault(); sendOwn(); }
      else if(a==="cancel"){ skip(); }
      else if(a==="submit"){ skip('[data-a="submit"]'); }
      else if(a==="next"){ finishMulti(); }
    },true);
    var form=root.querySelector(".lab-ask-own");
    if(form){
      form.addEventListener("submit",function(e){ e.preventDefault(); sendOwn(); });
      form.querySelector("textarea").addEventListener("input",function(){
        sizeOwn();
        $("s-question").dispatchEvent(new CustomEvent("question:own-draft",{detail:{id:S.sid,key:S.key,text:this.value}}));
      });
    }
    ["pointerup","pointercancel","pointerleave"].forEach(function(ev){ sheet.addEventListener(ev,function(){ sheet.querySelectorAll(".pressed").forEach(function(x){press(x,false);}); }); });
    pill.addEventListener("click",function(){ if(S.justDragged) return; if(S.state==="pill") go("open",true); });
    if(pillRO){ pillRO.observe(pill); var foot=D.querySelector(".sfoot"); if(foot) pillRO.observe(foot); }
  }
  function press(el,on){ el.classList.toggle("pressed",!!on); }
  function dragStart(e,el,fromPill){
    if(S.busy||(e.button!==undefined&&e.button>0)) return;
    S.drag={el:el,id:e.pointerId,x0:e.clientX,y0:e.clientY,base:curY(),moved:false,s:[{t:performance.now(),y:e.clientY}],pill:fromPill};
    /* перехват анимации: берём текущее значение и продолжаем от него */
    S.sheet.style.transition="none"; S.sheet.style.transform="translate3d(0,"+S.drag.base+"px,0)"; S.y=S.drag.base;
    D.addEventListener("pointermove",dragMove); D.addEventListener("pointerup",dragEnd); D.addEventListener("pointercancel",dragEnd);
  }
  function dragMove(e){
    var d=S.drag; if(!d||e.pointerId!==d.id) return;
    var dy=e.clientY-d.y0;
    if(!d.moved){ if(Math.abs(dy)<8&&Math.abs(e.clientX-d.x0)<8) return;
      if(Math.abs(e.clientX-d.x0)>Math.abs(dy)*1.4){ cleanupDrag(); return; }
      d.moved=true; try{ d.el.setPointerCapture(d.id); }catch(x){} S.sheet.querySelectorAll(".pressed").forEach(function(x){press(x,false);}); }
    var y=d.base+dy, H=hiddenY(); if(y<0) y=-rub(-y); else if(y>H) y=H+rub(y-H);
    S.y=y; S.sheet.style.transform="translate3d(0,"+y+"px,0)"; paintProgress(clamp(y/H,0,1.2),false);
    d.s.push({t:performance.now(),y:e.clientY}); if(d.s.length>8) d.s.shift();
  }
  function dragEnd(e){
    var d=S.drag; if(!d||(e&&e.pointerId!==d.id)) return;
    var moved=d.moved, H=hiddenY(), v=0;
    if(moved){
      var now=performance.now(), a=d.s[0]; for(var k=0;k<d.s.length;k++){ if(now-d.s[k].t<=90){ a=d.s[k]; break; } }
      var last=d.s[d.s.length-1], dt=Math.max(1,last.t-a.t); v=(last.y-a.y)/dt;   /* px/мс, вниз > 0 */
      if(now-last.t>120) v=0;
    }
    cleanupDrag();
    if(moved){
      S.justDragged=true; setTimeout(function(){S.justDragged=false;},60);
      var proj=S.y+v*180;                    /* проекция по скорости на 180 мс вперёд */
      go(proj>H*.45?"pill":"open",true);
    } else if(d.pill){ go("open",true); S.justDragged=true; setTimeout(function(){S.justDragged=false;},60); }
  }
  function cleanupDrag(){ var d=S.drag; S.drag=null; D.removeEventListener("pointermove",dragMove); D.removeEventListener("pointerup",dragEnd); D.removeEventListener("pointercancel",dragEnd);
    if(d&&d.moved){ try{ d.el.releasePointerCapture(d.id); }catch(x){} } else if(d&&S.sheet){ S.sheet.style.transition=""; S.sheet.style.transform="translate3d(0,"+S.y+"px,0)"; } }

  /* ---------- действия ---------- */
  function sizeOwn(){
    if(!S.root||!S.own) return;
    var ta=S.root.querySelector(".lab-ask-own-text"), cs=getComputedStyle(ta);
    ta.style.height="auto";
    ta.style.height=Math.min(ta.scrollHeight,parseFloat(cs.lineHeight)*5+parseFloat(cs.paddingTop)+parseFloat(cs.paddingBottom))+"px";
    S.root.querySelector('[data-act="own-send"]').disabled=S.busy||!ta.value.trim();
    S.root.classList.toggle("scrolls",S.sheet.querySelector(".lab-ask-list").scrollHeight>S.sheet.querySelector(".lab-ask-list").clientHeight+1);
  }
  function ownPending(pending){
    S.ownPending=pending; S.busy=pending; S.sheet.setAttribute("aria-busy",String(pending));
    var form=S.root.querySelector(".lab-ask-own");
    if(pending) form.setAttribute("aria-busy","true"); else form.removeAttribute("aria-busy");
    form.querySelector("textarea").readOnly=pending;
    S.sheet.querySelectorAll("button").forEach(function(b){ b.disabled=pending||(S.own&&b.classList.contains("lab-ask-opt")); });
    if(S.own) sizeOwn();
  }
  function ownEditor(open){
    if(!S.root||S.permission||S.busy) return;
    var before=S.sheet.getBoundingClientRect().height, form=S.root.querySelector(".lab-ask-own"), ta=form.querySelector("textarea");
    if(S.ownAnimation){ S.ownAnimation.cancel(); S.ownAnimation=null; }
    S.own=open; S.root.classList.toggle("own-open",open); form.hidden=!open;
    S.root.querySelector(".lab-ask-actions").hidden=open;
    S.root.querySelectorAll(".lab-ask-opt").forEach(function(b){ b.disabled=open; });
    if(open){ sizeOwn(); ta.focus({preventScroll:true}); }
    else{
      $("s-question").dispatchEvent(new CustomEvent("question:own-reset",{detail:{id:S.sid,key:S.key}}));
      ta.value=""; ta.style.height=""; ta.removeAttribute("aria-invalid");
      var error=form.querySelector(".lab-ask-own-error"); error.textContent=""; error.hidden=true;
      S.root.querySelector('[data-act="own"]').focus({preventScroll:true});
    }
    var after=S.sheet.getBoundingClientRect().height;
    if(!reduce()&&S.sheet.animate){ S.ownAnimation=S.sheet.animate([{height:before+"px"},{height:after+"px"}],{duration:ms("--m-state-t",302),easing:tok("--m-state")||"ease"}); }
  }
  async function sendOwn(){
    if(!S.root||!S.own||S.busy) return;
    var root=S.root, form=root.querySelector(".lab-ask-own"), ta=form.querySelector("textarea"), text=ta.value.trim();
    if(!text) return;
    var latest=parse(); if(!latest||sigOf(latest)!==S.sig) return;
    var error=form.querySelector(".lab-ask-own-error"); error.hidden=true; error.textContent=""; ta.removeAttribute("aria-invalid");
    ownPending(true);
    try{
      var detail={text:text,id:S.sid,key:S.key};
      $("s-question").dispatchEvent(new CustomEvent("question:answer",{detail:detail}));
      await detail.result;
    }finally{
      // Completion belongs to the session state, even if navigation rebuilt
      // this root. Never let an old promise change another session's sheet.
      sync();
    }
  }
  function skip(sel){  // «Пропустить», а на шаге review — «Отправить ответы»/«Отмена»: родная кнопка, лист уходит
    var root=S.root, n=nat(sel||'[data-a="cancel"]'); S.busy=true; S.dismissed=S.sig; if(n) n.click();
    if(S.root!==root) return;  // клик уже построил лист следующего вопроса — его не убираем
    go("pill",true,true); /* лист убирается, пилюля не нужна */
    S.pill.style.opacity="0"; S.root.dataset.state="gone"; setTimeout(function(){ if(S.root===root) teardown(); },380);
  }
  function choose(k){
    if(S.busy||!S.root||S.own) return;
    var o=S.opts[k]; if(!o) return;
    if(S.permission){
      var latest=parse(); if(!latest||sigOf(latest)!==S.sig) return;
      var native=nat('.opt[data-i="'+o.i+'"]'); if(native) native.click(); return;
    }
    if(S.multi){
      S.marks[k]=!S.marks[k];
      var r=S.sheet.querySelector('.lab-ask-opt[data-k="'+k+'"]'); r.setAttribute("aria-pressed",String(S.marks[k]));
      try{ if(typeof haptic==="function") haptic(); }catch(e){}
      var nb=$("s-question").querySelector('.opt[data-i="'+o.i+'"]'); if(nb) nb.click();
      return;
    }
    S.busy=true; S.dismissed=S.sig; var root=S.root;
    var row=S.sheet.querySelector('.lab-ask-opt[data-k="'+k+'"]');
    row.classList.add("picked"); S.root.classList.add("busy");
    try{ if(typeof haptic==="function") haptic(); }catch(e){}
    var title=row.querySelector(".lab-ask-t"), from=title.getBoundingClientRect();
    var nb2=$("s-question").querySelector('.opt[data-i="'+o.i+'"]'); if(nb2) nb2.click();
    if(S.more) return;  // лист ждёт следующий шаг поднятым (build сменит его на месте)
    setTimeout(function(){ send(o.label,from,root); }, reduce()?60:190);
  }
  function finishMulti(){
    if(S.busy) return; var labs=S.opts.filter(function(o,k){return S.marks[k];}).map(function(o){return o.label;});
    S.busy=true; S.dismissed=S.sig; S.root.classList.add("busy"); var root=S.root;
    var btn=S.sheet.querySelector('[data-act="next"]'); btn.classList.add("picked");
    var from=btn.getBoundingClientRect();
    var nb=nat('[data-a="next"]'); if(nb) nb.click();
    if(S.more) return;
    setTimeout(function(){ send(labs.join(", ")||"—",from,root); }, reduce()?60:190);
  }
  /* лист опускается; текст летит в исходящий пузырь внизу ленты (FLIP) */
  function send(text,from,root){
    var hist=$("s-hist"), body=$("s-body");
    /* ответ мог прийти раньше 190 мс и построить лист следующего вопроса: убираем только лист, по которому нажали */
    if(S.root===root){ go("pill",true,true); S.pill.style.opacity="0"; root.dataset.state="gone"; }
    if(!hist){ setTimeout(function(){ if(S.root===root) teardown(); },400); return; }
    var empty=hist.querySelector(".empty"); if(empty) empty.remove();
    var b=D.createElement("div"); b.className="me pending lab-ask-bubble"; b.textContent=text; b.style.opacity="0";
    hist.appendChild(b);
    try{ window.followUntil=Date.now()+4000; }catch(e){}
    body.scrollTop=body.scrollHeight;
    var tg=b.getBoundingClientRect();
    if(reduce()||!tg.width){ b.style.transition="opacity 160ms linear"; requestAnimationFrame(function(){ b.style.opacity="1"; }); setTimeout(function(){ if(S.root===root) teardown(); },300); return; }
    var c=D.createElement("div"); c.className="me lab-ask-fly"; c.textContent=text;
    c.style.cssText="position:fixed;left:"+tg.left+"px;top:"+tg.top+"px;width:"+tg.width+"px;height:"+tg.height+"px;margin:0;max-width:none;z-index:25;transform-origin:0 0;pointer-events:none;will-change:transform";
    D.body.appendChild(c);
    var sc=1.1, tx=from.left-13*sc-tg.left, ty=from.top+(from.height-tg.height*sc)/2-tg.top;
    var dur=ms("--m-travel-t",539), ease=tok("--m-travel")||"ease-out";
    var an=c.animate([
      {transform:"translate3d("+tx+"px,"+ty+"px,0) scale("+sc+")",backgroundColor:tok("--lab-lime")||"#d2fb4a",color:tok("--lab-lime-ink")||"#193006"},
      {transform:"translate3d(0,0,0) scale(1)",backgroundColor:getComputedStyle(b).backgroundColor,color:getComputedStyle(b).color}
    ],{duration:dur,easing:ease,fill:"both"});
    var fin=function(){ b.style.opacity=""; b.style.transition="none"; c.remove(); if(S.root===root) teardown(); };
    an.onfinish=fin; an.oncancel=fin;
  }

  /* ---------- синхронизация с продуктом ---------- */
  var pending=0;
  function sync(){
    if(pending) return; pending=1; requestAnimationFrame(function(){ pending=0; run(); });
  }
  function run(){
    var ss=$("session-screen"), sub=$("sub-screen");
    var vis=!!ss&&!ss.hidden&&(!sub||sub.hidden);
    if(!ss||ss.hidden) S.dismissed=null;
    if(S.permission&&S.root){
      S.busy=$("s-question").dataset.permBusy==="true";
      S.root.classList.toggle("busy",S.busy);
      S.sheet.setAttribute("aria-busy",String(S.busy));
      S.sheet.querySelectorAll("button").forEach(function(b){b.disabled=S.busy;});
    }
    var q=vis?parse():null;
    de.classList.toggle("lab-ask-has",!!(!ss.hidden&&$("s-question")&&parse()));
    if(!vis&&(S.permission||S.own||S.ownPending)){ teardown(true); return; }
    if(S.root&&!S.permission&&S.ownPending&&(!q||sigOf(q)!==S.sig||!q.pending)) ownPending(false);
    if(!q){ S.dismissed=null; if(S.root&&(!S.busy||S.more)) teardown(S.permission); return; }
    var sig=sigOf(q);
    if(S.dismissed===sig){ return; }
    if(!S.root||S.sig!==sig) build(q);
    else if(q.permission) permColor(S.root);
    if(!q.permission){
      if(q.ownState&&q.ownState.error){
        if(!S.own){ ownEditor(true); S.root.querySelector("textarea").value=q.ownState.text; sizeOwn(); }
        var error=S.root.querySelector(".lab-ask-own-error");
        error.textContent=q.ownState.error; error.hidden=false;
        S.root.querySelector("textarea").setAttribute("aria-invalid","true");
      }
      if(q.pending) ownPending(true);
    }
  }
  var mo=new MutationObserver(sync);
  function watch(){
    ["s-question","session-screen","sub-screen"].forEach(function(id){ var e=$(id); if(e) mo.observe(e,{childList:id==="s-question",subtree:id==="s-question",attributes:true,attributeFilter:["hidden","data-perm-busy","data-own-busy"]}); });
  }
  function viewportChanged(){ if(S.sheet){ if(S.state==="pill"){ go("pill",false); } placePill(); } }
  addEventListener("resize",viewportChanged);
  if(window.visualViewport){
    window.visualViewport.addEventListener("resize",viewportChanged);
    window.visualViewport.addEventListener("scroll",viewportChanged);
  }
  var pillRO=window.ResizeObserver?new ResizeObserver(placePill):null;
  D.addEventListener("keydown",function(e){
    if(S.root&&S.own&&S.state==="open"&&S.sheet.contains(e.target)&&e.key==="Enter"&&(e.metaKey||e.ctrlKey)){
      e.preventDefault(); e.stopImmediatePropagation(); sendOwn(); return;
    }
    if(e.key==="Escape"&&S.root&&S.own&&S.state==="open"){
      e.preventDefault(); e.stopImmediatePropagation(); if(!S.busy) ownEditor(false); return;
    }
    if(e.key==="Escape"&&S.root&&S.permission&&S.busy){ e.preventDefault(); e.stopImmediatePropagation(); return; }
    if(e.key==="Escape"&&S.root&&S.state==="open"&&!S.busy){
      e.preventDefault(); e.stopImmediatePropagation(); go("pill",true);
    }
  },true);
  /* ответ не дошёл: вопрос жив, возвращаем лист после ухода старого */
  D.addEventListener("question:fail",function(){
    S.dismissed=null;
    if(S.root&&S.more&&S.root.dataset.state==="open"){ S.busy=false; S.root.classList.remove("busy"); S.root.querySelectorAll(".picked").forEach(function(r){ r.classList.remove("picked"); }); }
    setTimeout(sync,450);
  });
  D.addEventListener("question:own",function(){ run(); ownEditor(true); });


  function setup(){ watch(); sync(); }
  sync();
  window.addEventListener("lab:change",sync);
  if(D.readyState==="loading") D.addEventListener("DOMContentLoaded",setup); else setup();
})();

/* ===== слой quota (lab-quota.js) ===== */
/* lab-quota.js — Квота, вариант Б: перекатывающиеся цифры (одометр) и капсулы-индикаторы.
   Значения читаются из DOM продукта (#quota, #s-quota) через
   MutationObserver; продуктовые блоки скрыты CSS, рядом рисуются свои (#lab-q, #lab-sq). */
(function(){
  var de=document.documentElement, STAG=24, DUR=769;
  var on=false, built=false, ranIn=false, mo=[], tiles={}, line=null, prod={}, shown={}, labelsOf={};
  var mq=window.matchMedia&&matchMedia("(prefers-reduced-motion: reduce)");
  function reduced(){return !!(mq&&mq.matches);}
  function ease(){var v=getComputedStyle(de).getPropertyValue("--m-numbers").trim();return v||"cubic-bezier(.2,.9,.3,1)";}
  function el(tag,cls,txt){var e=document.createElement(tag);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e;}
  function mat(node){var m=getComputedStyle(node).transform;if(!m||m==="none")return null;return m.match(/-?[\d.e-]+/g).map(Number);}

  /* ---- одометр ---- */
  function Counter(host){
    this.host=host;host.classList.add("lqn");host.setAttribute("aria-hidden","true");this.cols=[];this.val=null;
    for(var c=0;c<3;c++){
      var d=el("span","lqd z"),s=el("span","lqs");
      for(var i=0;i<11;i++)s.appendChild(el("b",null,i===0?"":String(i-1)));
      d.appendChild(s);host.appendChild(d);this.cols.push({d:d,s:s,idx:0});
    }
  }
  Counter.prototype.set=function(v,instant){
    var n=Math.max(0,Math.min(999,Math.round(v))),dg=[Math.floor(n/100)%10,Math.floor(n/10)%10,n%10],now=instant||reduced();
    for(var c=0;c<3;c++){
      var col=this.cols[c],blank=(c===0&&n<100)||(c===1&&n<10),idx=blank?0:dg[c]+1,s=col.s;
      col.d.classList.toggle("z",blank);
      if(col.idx===idx&&this.val!=null){continue;}
      var to=-idx*100/11,from=to;
      var m=mat(s);if(m&&s.offsetHeight)from=m[5]/s.offsetHeight*100;
      s.getAnimations().forEach(function(a){a.cancel();});
      s.style.transform="translateY("+to+"%)";
      col.idx=idx;
      if(now||Math.abs(from-to)<.05)continue;
      s.animate([{transform:"translateY("+from+"%)"},{transform:"translateY("+to+"%)"}],{duration:DUR,delay:c*STAG,easing:ease(),fill:"backwards"});
    }
    this.val=n;
  };
  function level(v){return v>=85?"hot":v>=60?"warn":"";}
  function tween(i,pct,instant){
    var tr=i.parentNode,w=tr.clientWidth||1,cur=i.getBoundingClientRect().width/w*100;
    i.getAnimations().forEach(function(a){a.cancel();});
    i.style.width=pct+"%";
    if(instant||reduced()||Math.abs(cur-pct)<.1)return;
    i.animate([{width:cur+"%"},{width:pct+"%"}],{duration:DUR,easing:ease(),fill:"backwards"});
  }

  /* ---- построение ---- */
  function longLabel(k,raw){var ru=/[чд]/i.test(raw||"")||/ru/i.test(de.lang||"");return k==="h5"?(ru?"5 часов":"5 hours"):(ru?"7 дней":"7 days");}
  function ensureTile(k,raw){
    var t=tiles[k];if(t)return t;
    var root=document.getElementById("lab-q");
    var tile=el("div","lqt"),lab=el("span","lql",longLabel(k,raw)),row=el("span","lqv"),num=el("span"),pc=el("span","lqp","%"),
        bd=el("span","lqb"),cap=el("span","lqc"),fill=el("i"),rs=el("span","lqr");
    row.appendChild(num);row.appendChild(pc);cap.appendChild(fill);cap.setAttribute("aria-hidden","true");
    [lab,row,bd,cap,rs].forEach(function(x){tile.appendChild(x);});
    tile.setAttribute("role","img");
    // порядок: 5 часов слева
    if(k==="h5")root.insertBefore(tile,root.firstChild);else root.appendChild(tile);
    t=tiles[k]={tile:tile,ctr:new Counter(num),fill:fill,bd:bd,rs:rs,hide:0,vis:false};
    t.ctr.set(0,true);fill.style.width="0%";
    return t;
  }
  function build(){
    var q=document.getElementById("quota"),sq=document.getElementById("s-quota");
    if(!q||!sq)return false;
    var root=el("div","lq");root.id="lab-q";root.hidden=true;q.parentNode.insertBefore(root,q.nextSibling);
    var l=el("div","squota lab-sq");l.id="lab-sq";l.hidden=true;sq.parentNode.insertBefore(l,sq.nextSibling);
    line={root:l,ctrs:{},xs:{}};
    root.appendChild(el("div","lqstale")).hidden=true;
    built=true;return true;
  }

  /* ---- чтение продукта ---- */
  function readList(){
    var q=document.getElementById("quota"),out={},stale="";
    if(q&&!q.hidden){
      [].forEach.call(q.querySelectorAll(".qbar"),function(b){
        var lab=(b.getAttribute("aria-label")||""),i=lab.lastIndexOf(":"),name=lab.slice(0,i).trim(),v=parseFloat(lab.slice(i+1));
        var k=/^5/.test(name)?"h5":/^7/.test(name)?"d7":null;if(!k||isNaN(v))return;
        var qv=b.nextElementSibling,sm=qv&&qv.querySelector("small");
        out[k]={v:v,raw:name,reset:sm?sm.textContent:""};
      });
      var last=q.lastElementChild;if(last&&last.tagName==="SPAN"&&last.style.gridColumn)stale=last.textContent;
    }
    return {vals:out,stale:stale,visible:!!(q&&!q.hidden&&Object.keys(out).length)};
  }
  function readLine(){
    var s=document.getElementById("s-quota"),out={},tail="";
    if(s&&!s.hidden){
      s.innerHTML.split(" · ").forEach(function(seg){
        var m=seg.match(/^([^<]*)<b[^>]*>\s*([\d.]+)\s*%\s*<\/b>(.*)$/);
        if(!m){ if(seg!=="Claude"&&seg.trim())tail=seg; return; }
        var lab=m[1].trim(),k=/^5/.test(lab)?"h5":/^7/.test(lab)?"d7":null;if(!k)return;
        out[k]={v:parseFloat(m[2]),raw:lab,extra:m[3].replace(/<[^>]*>/g,"").trim()};
      });
    }
    return {vals:out,tail:tail,visible:!!(s&&!s.hidden&&Object.keys(out).length)};
  }

  /* ---- отрисовка ---- */
  function showTile(k,info,over,instant){
    var t=ensureTile(k,info.raw),v=over!=null?over:info.v,prev=t.ctr.val;
    t.tile.setAttribute("aria-label",longLabel(k,info.raw)+": "+Math.round(v)+" %");
    t.rs.textContent=info.reset||"";t.rs.hidden=!info.reset;
    var lv=level(v);t.tile.classList.toggle("warn",lv==="warn");t.tile.classList.toggle("hot",lv==="hot");
    t.ctr.set(v,instant);tween(t.fill,Math.max(2,Math.min(100,v)),instant);
    var d=Math.round(v)-(prev==null?Math.round(v):prev);
    if(d>0&&!instant&&prev>0&&ranIn===2){
      t.bd.textContent="+"+d+" %";t.bd.classList.toggle("warn",lv==="warn");t.bd.classList.toggle("hot",lv==="hot");t.bd.classList.add("on");
      clearTimeout(t.hide);t.hide=setTimeout(function(){t.bd.classList.remove("on");},1200);
    }else if(d<0){clearTimeout(t.hide);t.bd.classList.remove("on");}
  }
  function renderList(over,instantAll){
    var r=readList(),root=document.getElementById("lab-q");if(!root)return;
    root.hidden=!r.visible;
    var first=!ranIn&&r.visible;
    Object.keys(r.vals).forEach(function(k){
      var info=r.vals[k];prod[k]=info.v;labelsOf[k]=info;
      var isNew=!tiles[k];
      showTile(k,info,over&&over[k]!=null?over[k]:null,instantAll||false);
      if(isNew&&!first&&!ranIn){/* ждём первого показа */}
    });
    Object.keys(tiles).forEach(function(k){if(!r.vals[k])tiles[k].tile.hidden=true;else tiles[k].tile.hidden=false;});
    var st=root.querySelector(".lqstale");st.textContent=r.stale;st.hidden=!r.stale;
    return r;
  }
  function renderLine(over,instant){
    var r=readLine();line.root.hidden=!r.visible;
    var parts=[];var host=line.root;
    if(!r.visible){return r;}
    var order=["h5","d7"].filter(function(k){return r.vals[k];});
    var sig=order.join(",")+"|"+order.map(function(k){return r.vals[k].raw+"/"+r.vals[k].extra;}).join(",")+"|"+r.tail;
    if(host.dataset.sig!==sig){
      host.dataset.sig=sig;host.textContent="";line.ctrs={};line.xs={};
      host.appendChild(document.createTextNode("Claude"));
      order.forEach(function(k){
        var x=el("span","lqx"),n=el("span"),inf=r.vals[k];
        host.appendChild(document.createTextNode(" · "+inf.raw+" "));
        x.appendChild(n);x.appendChild(document.createTextNode(" %"));host.appendChild(x);
        if(inf.extra)host.appendChild(document.createTextNode(" "+inf.extra));
        line.ctrs[k]=new Counter(n);line.xs[k]=x;line.ctrs[k].set(inf.v,true);
      });
      if(r.tail)host.appendChild(document.createTextNode(" · "+r.tail.trim()));
      host.setAttribute("aria-label","Claude "+order.map(function(k){return r.vals[k].raw+" "+Math.round(r.vals[k].v)+" %";}).join(", "));
      host.setAttribute("role","text");
    }
    order.forEach(function(k){
      var v=over&&over[k]!=null?over[k]:r.vals[k].v,lv=level(v),x=line.xs[k];
      x.classList.toggle("warn",lv==="warn");x.classList.toggle("hot",lv==="hot");
      line.ctrs[k].set(v,instant);
    });
    return r;
  }

  var override=null;
  function refresh(instant){
    if(!on||!built)return;
    var first=!ranIn;
    var r=readList();
    if(first&&r.visible){
      /* первый показ — сразу значения: накат от 0 шёл под заглушкой Telegram и был виден срезанными цифрами (M3); катятся только изменения */
      ranIn=2;renderList(override,true);
    }else renderList(override,instant);
    renderLine(override,instant);
  }
  function observe(){
    var ids=["quota","s-quota"];
    ids.forEach(function(id){
      var n=document.getElementById(id);if(!n)return;
      var o=new MutationObserver(function(){ if(override)return; refresh(false); });
      o.observe(n,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:["hidden","data-perm-busy"]});
      mo.push(o);
    });
  }

  function enable(){
    if(on)return;on=true;
    if(!built&&!build()){on=false;return;}
    observe();refresh(ranIn);
  }


  function boot(){ enable(); }
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",boot);else boot();
})();
