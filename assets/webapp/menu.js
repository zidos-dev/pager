/* Меню «Фокус»: перенос lab-menu.js, утверждённые menu=c, meta=a, press=a.
   Без лабораторных флагов и долгого нажатия в списке; действия вызывают кнопки продукта. */
(function(){
  "use strict";
  var de=document.documentElement, $=function(id){return document.getElementById(id);};
  var more=$("s-more"); if(!more) return;
  var RU=function(){return /^ru/i.test(de.lang||"");};
  var S={open:false,ctx:null,closeTimer:0,eatMore:false,sliding:false,hl:-1,rows:[]};
  var DICT={ru:{n:"Уведомления",ren:"Переименовать",arch:"Убрать в архив",rest:"Вернуть из архива",stop:"Остановить",on:"включены",off:"выключены"},
            en:{n:"Notifications",ren:"Rename",arch:"Archive",rest:"Restore from archive",stop:"Stop",on:"on",off:"off"}};
  var D=function(k){return DICT[RU()?"ru":"en"][k];};

  function hap(kind){ try{ var h=window.Telegram&&window.Telegram.WebApp&&window.Telegram.WebApp.HapticFeedback; if(!h) return; if(kind==="sel") h.selectionChanged(); else h.impactOccurred("light"); }catch(e){} }

  var root=document.createElement("div"); root.className="mn-root"; root.hidden=true;
  root.innerHTML='<div class="mn-catch"></div><div class="mn-back"></div><div class="mn-dyn"></div>';
  document.body.appendChild(root);
  var catcher=root.firstChild, dyn=root.lastChild;

  var P={
    bell:'<path d="M6 16v-5a6 6 0 1 1 12 0v5l1.5 2h-15L6 16Z"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/>',
    bellOff:'<path d="M6 16v-5a6 6 0 0 1 .8-3M9 5.2A6 6 0 0 1 18 11v5l1.5 2H8"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/><path d="M4 4l16 16"/>',
    pencil:'<path d="M4 20l1-4L16.5 4.5a2.1 2.1 0 0 1 3 3L8 19l-4 1Z"/><path d="M14.5 6.5l3 3"/>',
    archive:'<path d="M3.5 6.5h17v3.5h-17z"/><path d="M5 10v8a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 18v-8"/><path d="M10 14h4"/>',
    restore:'<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4.5V8.5h4"/>',
    stop:'<circle cx="12" cy="12" r="9"/><rect x="9" y="9" width="6" height="6" rx="1.2"/>'
  };
  function ic(n){ return '<svg class="mn-ic" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'+P[n]+'</svg>'; }
  var esc=function(t){return String(t==null?"":t).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});};

  /* ---------- модель: что показать ---------- */
  function model(){
    var w=$("a-watch"), val=(w.querySelector(".aval")||{}).textContent||"";
    var lab=Array.prototype.filter.call(w.childNodes,function(n){return n.nodeType===3;}).map(function(n){return n.textContent;}).join("").trim()||D("n");
    var at=$("a-arch").textContent.trim();
    return {watched:/^(on|вкл)/i.test(val.trim()),wlabel:lab,wval:val.trim(),rlabel:$("a-rename").textContent.trim()||D("ren"),
      restore:/restore|верн/i.test(at),alabel:at,stopHidden:$("a-stop").hidden,slabel:$("a-stop").textContent.trim()||D("stop"),
      meta:($("s-meta").textContent||"").trim()};
  }
  function rowHtml(r,i){
    return '<button type="button" class="mn-row'+(r.dng?" dng":"")+'" role="menuitem" tabindex="-1" data-k="'+r.k+'" style="--i:'+i+'">'+ic(r.icon)+
      '<span class="mn-tx"><b>'+esc(r.label)+'</b>'+(r.sub?'<small>'+esc(r.sub)+'</small>':'')+'</span></button>';
  }
  function rowsOf(m){
    var g1=[{k:"watch",icon:m.watched?"bell":"bellOff",label:m.wlabel,sub:m.wval},{k:"rename",icon:"pencil",label:m.rlabel},{k:"arch",icon:m.restore?"restore":"archive",label:m.alabel}];
    var g2=m.stopHidden?[]:[{k:"stop",icon:"stop",label:m.slabel,dng:true}];
    return [g1,g2];
  }

  /* ---------- построение и геометрия ---------- */
  var menu=null;
  function build(ctx){
    var m=model(), gs=rowsOf(m), i=0;
    dyn.innerHTML="";
    menu=document.createElement("div"); menu.className="mn-menu mn-glass"; menu.id="session-menu"; menu.setAttribute("role","menu");
    menu.setAttribute("aria-label",more.getAttribute("aria-label")||"");
    gs.forEach(function(g,gi){
      if(!g.length) return;
      var el=document.createElement("div"); el.className="mn-grp"; el.setAttribute("role","group");
      var h="";
      if(gi===0&&m.meta) h+='<p class="mn-meta" role="presentation">'+esc(m.meta)+'</p>';
      g.forEach(function(r){ h+=rowHtml(r,i++); });
      el.innerHTML=h; menu.appendChild(el);
    });
    dyn.appendChild(menu);
    S.rows=Array.prototype.slice.call(menu.querySelectorAll(".mn-row"));
    S.hl=-1;
  }
  function place(ctx){
    var vw=innerWidth, vh=innerHeight, W=Math.min(250,vw-24);
    var r=ctx.rect, gap=8;
    var h=menu.offsetHeight, left=Math.max(12,Math.min(r.right-W,vw-12-W)), top=r.bottom+gap, above=false;
    if(top+h>vh-12){ if(r.top-gap-h>=12){ top=r.top-gap-h; above=true; } else top=Math.max(12,vh-12-h); }
    menu.style.left=left+"px"; menu.style.top=top+"px";
    var ax=Math.max(0,Math.min(W,(r.left+r.width/2)-left));
    menu.style.setProperty("--mn-ox",ax+"px"); menu.style.setProperty("--mn-oy",above?"100%":"0px");
  }
  function addClone(ctx){
      var b=document.createElement("button"); b.type="button"; b.className="mn-clone"; b.tabIndex=-1; b.setAttribute("aria-hidden","true"); b.innerHTML=more.innerHTML;
      var r2=ctx.rect; b.style.cssText="left:"+r2.left+"px;top:"+r2.top+"px;width:"+r2.width+"px;height:"+r2.height+"px";
      b.addEventListener("click",function(e){ close({focus:e.detail===0}); });
      dyn.insertBefore(b,menu); de.classList.add("mn-opening");
  }

  /* ---------- открыть / закрыть ---------- */
  function open(ctx){
    clearTimeout(S.closeTimer);
    var closing=!root.hidden&&!S.open;
    S.open=true;
    if(closing){ refresh(); }   /* прерванное закрытие: продолжаем с текущего состояния, без пересборки */
    else{
      cleanup();
      ctx.rect=ctx.rect||more.getBoundingClientRect();
      S.ctx=ctx; root.hidden=false; build(ctx); addClone(ctx); place(ctx);
      void root.offsetWidth;
    }
    root.classList.add("mn-on");
    more.setAttribute("aria-expanded","true");
    hap();
    if(ctx.kb&&S.rows[0]) S.rows[0].focus({preventScroll:true});
  }
  function cleanup(){
    de.classList.remove("mn-opening"); dyn.innerHTML=""; menu=null; S.rows=[]; S.hl=-1;
  }
  function close(o){
    o=o||{};
    if(!S.open) return;
    S.open=false; S.sliding=false;
    root.classList.remove("mn-on");
    more.setAttribute("aria-expanded","false");
    clearTimeout(S.closeTimer);
    S.closeTimer=setTimeout(function(){ root.hidden=true; cleanup(); },320);
    if(o.focus) try{ more.focus({preventScroll:true}); }catch(e){}
  }
  function closeNow(){
    cancelHold(); clearTimeout(S.closeTimer); S.open=false; S.sliding=false; sw=null;
    root.classList.remove("mn-on"); root.hidden=true;
    more.setAttribute("aria-expanded","false"); cleanup();
  }
  function refresh(){
    if(!menu||!S.ctx) return;
    var m=model(), w=menu.querySelector('[data-k="watch"]');
    if(w){ w.querySelector("b").textContent=m.wlabel; var sm=w.querySelector("small"); if(sm) sm.textContent=m.wval; w.querySelector("svg").outerHTML=ic(m.watched?"bell":"bellOff"); }
    var a=menu.querySelector('[data-k="arch"]');
    if(a){ a.querySelector("b").textContent=m.alabel; a.querySelector("svg").outerHTML=ic(m.restore?"restore":"archive"); }
  }
  /* значения строк следят за кнопками продукта (нажатие «Уведомлений» перерисовывает вторую строку) */
  try{
    var mo=new MutationObserver(function(){ if(S.open) refresh(); });
    ["a-watch","a-arch","a-rename"].forEach(function(id){ mo.observe($(id),{childList:true,subtree:true,characterData:true}); });
  }catch(e){}

  /* Действия остаются у продукта; переключатель сохраняет меню открытым. */
  function activate(row,kb){
    var k=row.dataset.k;
    if(k==="watch"){ $("a-watch").click(); return; }
    close({focus:!!kb&&k!=="rename"}); hap();
    $({rename:"a-rename",arch:"a-arch",stop:"a-stop"}[k]).click();
  }
  dyn.addEventListener("click",function(e){
    var row=e.target.closest&&e.target.closest(".mn-row"); if(!row||!S.open) return;
    e.preventDefault(); activate(row,e.detail===0);
  });
  catcher.addEventListener("pointerdown",function(e){ e.preventDefault(); close({focus:false}); });

  /* ---------- клик по «⋯» ---------- */
  window.addEventListener("click",function(e){
    var el=e.target; if(!el||!el.closest) return;
    // До capture-обработчика Clio: закрытие меню не должно запускать свёртку экрана.
    if(S.open&&el.closest("#session-screen [data-back]")){
      e.stopImmediatePropagation(); e.preventDefault(); close({focus:true}); return;
    }
    if(!el.closest("#s-more")) return;
    e.stopImmediatePropagation(); e.preventDefault();
    if(S.eatMore){ S.eatMore=false; return; }
    if(S.open) close({focus:e.detail===0}); else open({kb:e.detail===0});
  },true);

  /* ---------- клавиатура ---------- */
  window.addEventListener("keydown",function(e){
    if(!S.open) return;
    var k=e.key;
    if(k==="Escape"){ e.preventDefault(); e.stopPropagation(); close({focus:true}); return; }
    if(k==="Tab"){ close({focus:true}); return; }
    var n=S.rows.length; if(!n) return;
    var i=S.rows.indexOf(document.activeElement);
    if(k==="ArrowDown"){ i=i<0?0:(i+1)%n; }
    else if(k==="ArrowUp"){ i=i<0?n-1:(i-1+n)%n; }
    else if(k==="Home"){ i=0; } else if(k==="End"){ i=n-1; } else return;
    e.preventDefault(); S.rows[i].focus({preventScroll:true});
  },true);

  /* ---------- скольжение пальца и свайп вниз ---------- */
  function setHl(i){
    if(i===S.hl) return;
    S.rows.forEach(function(r,j){ r.classList.toggle("hl",j===i); });
    if(i>=0) hap("sel"); S.hl=i;
  }
  function slideMove(x,y){
    var el=document.elementFromPoint(x,y), row=el&&el.closest&&el.closest(".mn-row");
    setHl(row?S.rows.indexOf(row):-1);
  }
  function slideEnd(){
    S.sliding=false;
    var i=S.hl, row=i>=0?S.rows[i]:null; setHl(-1);
    if(row&&S.open) activate(row);
  }
  var hold=null;
  more.addEventListener("pointerdown",function(e){
    if(S.open||(e.pointerType==="mouse"&&e.button!==0)) return;
    hold={x:e.clientX,y:e.clientY,t:setTimeout(function(){ if(!hold) return; S.sliding=true; S.eatMore=true; setTimeout(function(){S.eatMore=false;},1500); open({}); },260)};
  });
  function cancelHold(){ if(hold){ clearTimeout(hold.t); hold=null; } }
  addEventListener("pointermove",function(e){
    if(S.sliding&&S.open){ slideMove(e.clientX,e.clientY); return; }
    if(hold&&Math.hypot(e.clientX-hold.x,e.clientY-hold.y)>10) cancelHold();
  },true);
  addEventListener("pointerup",function(){ cancelHold(); if(S.sliding){ setTimeout(slideEnd,0); } },true);
  addEventListener("pointercancel",function(){ cancelHold(); if(S.sliding){ S.sliding=false; setHl(-1); } },true);
  var sw=null;
  dyn.addEventListener("pointerdown",function(e){ if(S.sliding) return; sw={x:e.clientX,y:e.clientY}; });
  addEventListener("pointermove",function(e){ if(!sw||!S.open) return; var dy=e.clientY-sw.y, dx=e.clientX-sw.x; if(dy>48&&dy>Math.abs(dx)*1.5){ sw=null; close({focus:false}); } },true);
  addEventListener("pointerup",function(){ sw=null; },true);

  /* Нативная BackButton закрывает меню первым; уход на другой экран убирает слой. */
  document.addEventListener("session:back",function(e){
    if(S.open){ e.preventDefault(); close({focus:true}); }
  });
  // popstate и Telegram BackButton проходят через session:back в продукте.
  var screen=$("session-screen");
  var navigationObserver=new MutationObserver(function(){
    if(screen.hidden||!$("sub-screen").hidden||!$("new-screen").hidden) closeNow();
  });
  [screen,$("sub-screen"),$("new-screen")].forEach(function(el){
    navigationObserver.observe(el,{attributes:true,attributeFilter:["hidden"]});
  });
  more.setAttribute("aria-controls","session-menu");
  addEventListener("resize",closeNow);
})();
