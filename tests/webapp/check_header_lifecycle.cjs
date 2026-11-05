const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(process.env.APP_DIR || path.join(__dirname, '../../assets/webapp'), 'index.html'),'utf8');
const code = source.slice(source.indexOf('let appReloading ='), source.indexOf('/* ---------- список')) +
 source.slice(source.indexOf('async function loadSession(){'), source.indexOf('$("s-chain").addEventListener')) +
 source.slice(source.indexOf('/* опрос */'), source.indexOf('async function doLogin')); 
function setup() {
  const state = {length:100, calls:[], reloads:0, toasts:[], inputs:[], overlay:null, files:[], mode:'ok', listeners:{}, intervals:[], clearedTimeouts:[], clearedIntervals:[], renders:0, failures:[], pending:[]};
  const document = {hidden:false, activeElement:null,
    querySelectorAll:()=>state.inputs, querySelector:()=>state.overlay,
    addEventListener:(name,cb)=>{state.listeners[name]=cb;}};
  const context=vm.createContext({document, location:{reload:()=>{assert.equal(state.pending.length,0,'reload before abort handlers settled');state.reloads++;}}, AbortController,
    window:{addEventListener:(name,cb)=>{state.listeners[name]=cb;}},
    atts:state.files,initData:'',deviceToken:null,DEV:true,current:'synthetic',permVersion:0,permPending:null,ownAnswerVersion:0,ownAnswerBusy:()=>false,ownAnswerRequests:new Map(),ownQuestionKey:()=>'',view:'session',sess:{group:'work'},followUntil:0,
    renderSession:()=>state.renders++,setLang:()=>{},loadList:async()=>{},loadSub:async()=>{},haptic:()=>{},
    setTimeout:()=>1, clearTimeout:id=>state.clearedTimeouts.push(id), clearInterval:id=>state.clearedIntervals.push(id), setInterval:(cb,ms)=>{state.intervals.push({cb,ms});return 120;}, toastTimer:0,
    $:()=>({files:state.files}), t:key=>({updateAvailable:'Доступно обновление',updateNow:'Обновить'}[key]),
    toast:(...args)=>state.toasts.push(args),
    fetch:async(url,opts)=>{
      state.calls.push({url,...opts});
      if(state.defer && state.defer(url,opts)) return new Promise((resolve,reject)=>{
        const entry={url,resolve,reject};state.pending.push(entry);
        opts.signal.addEventListener('abort',()=>{state.pending.splice(state.pending.indexOf(entry),1);reject(new Error('abort during reload'));},{once:true});
      });
      if(!url.startsWith('/app/')) return {ok:true,status:200,json:async()=>({id:'synthetic',group:'work'})};
      if(state.mode==='network') throw new Error('offline');
      // ee2488f: версия = длина тела по GET; «missing»/«invalid» — тело не читается
      return {ok:state.mode!=='503', arrayBuffer:async()=>{if(state.mode==='missing'||state.mode==='invalid')throw new Error('body');return new ArrayBuffer(state.length);}};
    }
  });
  vm.runInContext(code,context);
  state.check=(initial=false)=>vm.runInContext(`checkAppVersion(${initial})`, context);
  state.baseline=()=>vm.runInContext('appVersion',context);
  state.run=code=>vm.runInContext(code,context);
  return {state,document};
}
(async()=>{
  let passed=0;
  {
    const {state:s}=setup(); await s.check(true); assert.equal(s.baseline(),600);
    assert.deepEqual(s.calls.map(x=>x.url).sort(),['layers.css','layers.js','index.html','menu.css','menu.js','voice.js'].map(x=>'/app/'+x).sort());
    assert(s.calls.every(x=>!x.method&&x.cache==='no-store'));
    await s.check(); assert.equal(s.reloads,0); s.length=101; await s.check(); assert.equal(s.reloads,1); passed++;
  }
  for(const guard of ['draft','focus','menu','question','permission','file']){
    const {state:s,document:d}=setup(); await s.check(true); s.length=110;
    if(guard==='draft')s.inputs=[{value:'draft'}];
    else if(guard==='focus'){s.inputs=[{value:''}];d.activeElement=s.inputs[0];}
    else if(guard==='file')s.files.push({name:'pending.txt'});
    else s.overlay={kind:guard};
    await s.check(); assert.equal(s.reloads,0,guard); assert.equal(s.toasts.length,1,guard);
    assert.equal(s.toasts[0][0],'Доступно обновление'); assert.equal(s.toasts[0][1],'Обновить');
    await s.toasts[0][2](); await new Promise(setImmediate); assert.equal(s.reloads,1); passed++;
  }
  for(const failure of ['503','network','missing','invalid']){
    const {state:s}=setup(); await s.check(true); s.mode=failure;s.length=150;await s.check();
    assert.equal(s.reloads,0);assert.equal(s.toasts.length,0);assert.equal(s.baseline(),600);
    s.mode='ok';await s.check();assert.equal(s.reloads,1);passed++;
  }
  {
    const {state:s,document:d}=setup();await s.check(true);s.length=150;d.hidden=true;let before=s.calls.length;
    await s.check();s.intervals[0].cb();assert.equal(s.calls.length,before);assert.equal(s.intervals[0].ms,120000);
    d.hidden=false;await s.listeners.visibilitychange();await new Promise(setImmediate);assert.equal(s.reloads,1);passed++;
  }
  {
    const {state:s,document:d}=setup();d.hidden=true;await s.check(true);assert.equal(s.baseline(),600);
    d.hidden=false;await Promise.all([s.check(),s.check()]);assert.equal(s.calls.length,12);passed++;
  }
  {
    const {state:s}=setup();await s.check(true);
    s.defer=url=>url.startsWith('/api/');
    const poll=s.run('poll()');assert.equal(s.pending.length,1);
    s.length=120;await s.check();await poll;
    assert.equal(s.reloads,1);assert.equal(s.renders,0,'late session render');
    assert(s.clearedTimeouts.length);assert.deepEqual(s.clearedIntervals,[120]);
    const before=s.calls.length;await s.run('poll()');await s.check();
    await s.listeners.visibilitychange();s.intervals[0].cb();
    assert.equal(s.calls.length,before,'requests restarted after reload');passed++;
  }
  {
    const {state:s}=setup();await s.check(true);s.inputs=[{value:'draft'}];s.length=130;
    await s.check();s.defer=()=>true;
    const pendingCheck=s.check();assert.equal(s.pending.length,6);
    await s.toasts[0][2]();await pendingCheck;await new Promise(setImmediate);
    assert.equal(s.reloads,1);assert.equal(s.toasts.length,1);passed++;
  }
  {
    const {state:s}=setup();await s.check(true);s.defer=url=>url.startsWith('/api/');
    const poll=s.run('poll()');s.listeners.pagehide({persisted:false});await poll;
    assert.equal(s.renders,0);assert.equal(s.pending.length,0);assert.equal(s.reloads,0);passed++;
  }
  {
    const {state:s}=setup();await s.check(true);s.listeners.pagehide({persisted:true});
    s.length=140;await s.check();assert.equal(s.reloads,1,'BFCache pagehide disabled updater');passed++;
  }
  console.log(`PASS ${passed} updater/lifecycle VM cases (no browser; WebKit acceptance remains pending)`);
})().catch(e=>{console.error(e);process.exit(1);});
