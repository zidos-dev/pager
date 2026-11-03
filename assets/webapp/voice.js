/* Кнопка микрофона в поле сессии: способность сервера — поле voice в GET /api/sessions,
   запись целиком уходит на POST /api/voice (тело — Blob audio/*, заголовки как у загрузки файлов),
   распознанный текст встаёт в поле на правку — в сессию ничего не отправляется.
   Продукт (index.html) изолирован в IIFE, поэтому, как и menu.js, скрипт самодостаточен:
   свои строки ru/en, свой тост поверх #toast, смену сессии видит по #s-meta (cwd · id). */
(function(){
"use strict";
const $=id=>document.getElementById(id);
const STR={
  ru:{mic:"Голосовой ввод",fail:"Распознавание речи недоступно",denied:"Нет доступа к микрофону",
      tooBig:"Запись больше 10 МБ",auth:"Вход устарел — закройте и откройте мини-апп заново из Telegram",
      net:"Сеть недоступна, попробуйте ещё раз"},
  en:{mic:"Voice input",fail:"Speech recognition is unavailable",denied:"No microphone access",
      tooBig:"The recording is over 10 MB",auth:"Your sign-in has expired — close the mini app and open it again from Telegram",
      net:"No connection, please try again"}
};
const lang=()=>/^ru/i.test(document.documentElement.lang||"")?"ru":"en";  // тот же признак, что applyI18n в index.html
const t=k=>STR[lang()][k];

const compose=document.querySelector(".compose"),sendBtn=$("s-send"),ta=$("s-text");
if(!compose||!sendBtn||!ta)return;

/* index.html заморожен, поэтому стили состояния кнопки живут здесь */
const style=document.createElement("style");
style.textContent='#s-mic[aria-pressed="true"]{color:var(--danger)}#s-mic:disabled{color:var(--muted)}';
document.head.appendChild(style);

const mic=document.createElement("button");
mic.type="button";mic.id="s-mic";mic.className="clip";mic.hidden=true;
mic.setAttribute("aria-pressed","false");
mic.innerHTML='<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2.5a3 3 0 0 0-3 3v3a3 3 0 0 0 6 0v-3a3 3 0 0 0-3-3z"/><path d="M5 11.5a7 7 0 0 0 14 0"/><path d="M12 18.5v3"/></svg>';
compose.insertBefore(mic,sendBtn);

const haptic=()=>{try{const h=window.Telegram&&window.Telegram.WebApp&&window.Telegram.WebApp.HapticFeedback;h&&h.impactOccurred("light");}catch(e){}};
/* авторизация — как у загрузки файлов (index.html, /upload); токен читаем заново на каждую отправку */
function headers(){
  const tg=window.Telegram&&window.Telegram.WebApp,init=(tg&&tg.initData)||"";
  const h={};
  if(init)h["X-Tg-Init-Data"]=init;
  else{let tok=null;try{tok=localStorage.getItem("cc-device");}catch(e){}if(tok)h["X-Device-Token"]=tok;}
  if(!init&&/^(127\.0\.0\.1|localhost)$/.test(location.hostname))h["X-Dev"]="1";
  return h;
}
let toastTimer=0;
function toast(msg){  /* тот же элемент и анимация, что у toast() продукта */
  const el=$("toast");if(!el)return;
  el.innerHTML="";const s=document.createElement("span");s.textContent=msg;el.appendChild(s);
  el.hidden=false;el.classList.add("off");void el.offsetWidth;el.classList.remove("off");
  clearTimeout(toastTimer);
  toastTimer=setTimeout(()=>{el.classList.add("off");setTimeout(()=>{el.hidden=true;},220);},2600);
}
const hasAtt=()=>!!document.querySelector("#s-attach .att");  // черновые вложения: видна «Отправить», не микрофон
function autosize(){  /* после программной вставки текста — как autosize продукта */
  ta.style.height="auto";ta.style.height=Math.min(160,ta.scrollHeight)+"px";
  sendBtn.disabled=!ta.value.trim()&&!hasAtt();
}

/* ---------- видимость: сервер умеет голос, браузер умеет записывать, поле пусто ---------- */
const voiceOk=()=>document.documentElement.dataset.voice==="1";  // выставляет loadList продукта
const canRec=()=>!!(window.MediaRecorder&&navigator.mediaDevices&&navigator.mediaDevices.getUserMedia);
function update(){
  const label=t("mic");
  if(mic.getAttribute("aria-label")!==label)mic.setAttribute("aria-label",label);
  const micOn=mode!=="idle"||(voiceOk()&&canRec()&&!ta.value.trim()&&!hasAtt());
  mic.hidden=!micOn;sendBtn.hidden=micOn;  // микрофон и «Отправить» делят одно место в поле
}

/* ---------- запись ---------- */
let mode="idle";  // idle → ask → rec → stop → send
let rec=null,stream=null,chunks=[],autostop=0,watch=0,ctx="";
const metaNow=()=>{const el=$("s-meta");return el?String(el.textContent||""):"";};
const gone=()=>{const sc=$("session-screen");return !sc||sc.hidden||metaNow()!==ctx;};  // ушли или сменилась сессия
function stopStream(){if(stream){stream.getTracks().forEach(x=>{try{x.stop();}catch(e){}});stream=null;}}
function reset(){
  clearInterval(watch);watch=0;clearTimeout(autostop);autostop=0;
  if(rec){try{rec.onstop=rec.ondataavailable=rec.onerror=null;}catch(e){}if(rec.state!=="inactive"){try{rec.stop();}catch(e){}}rec=null;}
  stopStream();chunks=[];mode="idle";
  mic.disabled=false;mic.setAttribute("aria-pressed","false");update();
}
function watchdog(){  // запись (и распознавание) живут только в своей сессии
  if(mode!=="idle"&&gone())reset();
}
async function start(){
  ctx=metaNow();mode="ask";watch=setInterval(watchdog,1000);update();
  let audio;
  try{audio=await navigator.mediaDevices.getUserMedia({audio:true});}
  catch(e){if(mode==="ask")toast(t("denied"));reset();return;}  // отказ микрофона
  if(mode!=="ask"){audio.getTracks().forEach(x=>x.stop());return;}  // пока просили доступ — всё отменилось
  try{rec=new MediaRecorder(audio);}
  catch(e){audio.getTracks().forEach(x=>x.stop());toast(t("fail"));reset();return;}
  stream=audio;chunks=[];
  rec.ondataavailable=ev=>{if(ev.data&&ev.data.size)chunks.push(ev.data);};
  rec.onstop=onStopped;
  rec.onerror=()=>{if(mode==="rec")reset();};
  try{rec.start();}
  catch(e){toast(t("fail"));reset();return;}
  mode="rec";mic.setAttribute("aria-pressed","true");haptic();
  autostop=setTimeout(()=>{if(mode==="rec")stop();},5*60*1000);  // автостоп
  update();
}
function stop(){  // повторный тап: остановить и отправить на распознавание
  if(mode!=="rec")return;
  mode="stop";mic.disabled=true;mic.setAttribute("aria-pressed","false");
  clearTimeout(autostop);autostop=0;update();
  try{rec.stop();}catch(e){onStopped();}
}
function onStopped(){
  if(mode!=="stop")return;
  const parts=chunks;chunks=[];
  const mt=rec&&/^audio\//i.test(rec.mimeType||"")?rec.mimeType:"audio/webm";  // тип — от рекордера
  stopStream();
  const blob=new Blob(parts,{type:mt});
  if(!blob.size){toast(t("fail"));reset();return;}
  if(gone()){reset();return;}  // пока останавливали запись, ушли из сессии — выбросить
  mode="send";update();
  post(blob);
}
async function post(blob){
  let r;
  try{r=await fetch("/api/voice",{method:"POST",headers:headers(),body:blob});}
  catch(e){if(mode==="send")toast(t("net"));reset();return;}
  const j=await r.json().catch(()=>({}));
  if(mode!=="send")return;  // прервали, пока шло распознавание
  if(r.status===401){try{localStorage.removeItem("cc-device");}catch(e){}toast(t("auth"));reset();return;}  // как в остальном коде
  if(r.ok&&j&&j.ok){
    const text=String(j.text||"").trim();
    if(text&&!gone()){  // пусто — заменить, иначе добавить через пробел
      const v=ta.value.trim();
      ta.value=v?v+" "+text:text;
      autosize();ta.focus();
    }
    reset();return;
  }
  toast(j&&j.error?String(j.error):(r.status===413?t("tooBig"):t("fail")));  // поле не трогаем
  reset();
}

mic.addEventListener("click",()=>{
  if(mic.disabled)return;
  if(mode==="rec")stop();
  else if(mode==="idle"&&voiceOk()&&canRec())start().catch(()=>{});
});

/* пустоту поля ловим на input и focus (продукт меняет поле программно, но setMode даёт фокус)
   и спокойным интервалом; способность сервера обновляем изредка, при возврате на страницу и при входе в сессию */
ta.addEventListener("input",update);
ta.addEventListener("focus",update);
setInterval(update,1200);
update();
})();
