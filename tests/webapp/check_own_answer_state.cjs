// Browser-free race checks against the actual product and layers functions.
// APP_DIR=/path/to/assets/webapp node check_own_answer_state.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const app = process.env.APP_DIR || path.join(__dirname, '../../assets/webapp');
const html = fs.readFileSync(path.join(app, 'index.html'), 'utf8');
const layers = fs.readFileSync(path.join(app, 'layers.js'), 'utf8');
for (const script of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new vm.Script(script[1]);
new vm.Script(layers);
function section(source, from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert(start >= 0 && end > start, `missing source section ${from}`);
  return source.slice(start, end);
}
const q = {step: 0, n: 2, question: 'Original?', options: [{label: 'One'}]};
const next = {...q, step: 1, question: 'Next?'};
function setup() {
  const calls = [], rendered = [], effects = [], elements = {};
  const sandbox = {Map, JSON, encodeURIComponent, setTimeout() {},
    api(url, body) { return new Promise((resolve, reject) => calls.push({url, body, resolve, reject})); },
    $(id) { return elements[id] ||= {dataset: {}, style: {}, value: '', blur() {}, addEventListener() {}}; },
    document: {querySelectorAll: () => []},  // openSession marks .card.cur in the list (f68c8b8)
    t: key => key, esc: String, haptic: value => effects.push(['haptic', value]),
    toast: value => effects.push(['toast', value]), answered: () => {},
    push(value) { sandbox.view = value; }, setMode() {}, quotaLine() {}, autosize() {}, clearAtt() {}, renderAtt() {}, atts: [], MODES: {send: 'message'}, appReloading: false,
    renderPermission() {},
    histHold: false, peelHash() {}, setSessionAddr() {},  // l5: адрес #s= ведёт openSession, здесь не проверяется
    renderSession() { rendered.push(sandbox.sess.id); vm.runInContext('renderQuestion()', context); },
    view: 'session', current: 'a', sess: {id: 'a', question: q},
    data: {sessions: [{id: 'a'}, {id: 'b'}]}, lastHist: '', histDefer: false, selInHist: () => false,  // feed selection deferral (13.11)
  };
  const context = vm.createContext(sandbox);
  const run = code => vm.runInContext(code, context);
  run(section(html, 'let permPending =', 'function permShort('));
  run(section(html, 'function renderQuestion(){', '$("s-question").addEventListener("click"'));
  run(section(html, 'async function submitOwnAnswer(', '/* A DOM event bridges'));
  run(section(html, '/* черновики по сессиям', 'async function openSession('));  // 12c4a34: черновик на сессию
  run(section(html, 'async function openSession(', '$("s-chain").addEventListener'));
  // Include the actual reset/draft handlers as well.
  const handlers = {};
  sandbox.$('s-question').addEventListener = (type, fn) => { handlers[type] = fn; };
  run(section(html, '$("s-question").addEventListener("question:own-reset"', 'async function answerPermission('));
  return {sandbox, calls, rendered, effects, run, handlers,
    start() { return run('submitOwnAnswer("draft", "a", ownQuestionKey(sess.question))'); },
    async open(id, question) {
      const promise = run(`openSession(${JSON.stringify(id)})`);
      calls.at(-1).resolve({id, question}); await promise;
    },
  };
}
async function races() {
  let count = 0;
  for (const reply of ['ok', 'next', 'fail', 'network']) {
    for (const returnPending of [true, false]) {
      const t = setup(), pending = t.start(), post = t.calls[0];
      assert.equal(post.url, 'session/a/answer');
      assert.equal(JSON.stringify(post.body), '{"action":"own","text":"draft","step":0}');
      await t.open('b', {...q, question: 'Other?'});
      if (returnPending) {
        await t.open('a', q);
        assert.equal(t.sandbox.sess.question.question, 'Original?');
        assert.equal(t.sandbox.$('s-question').dataset.ownBusy, 'true');
        assert.equal(t.run('ownAnswerBusy("a")'), true);
      }
      const rendered = t.rendered.length, effects = t.effects.length, requests = t.calls.length;
      if (reply === 'network') post.reject(Error('offline'));
      else post.resolve(reply === 'fail' ? {ok: false, error: 'failure'} : {ok: true, question: reply === 'next' ? next : null});
      await pending;
      assert.equal(t.run('ownAnswerBusy("a")'), false);
      if (!returnPending) {
        assert.equal(t.sandbox.sess.question.question, 'Other?');
        assert.equal(t.rendered.length, rendered);
        assert.equal(t.effects.length, effects);
        assert.equal(t.calls.length, requests, 'offscreen completion refreshed another session');
        await t.open('a', reply === 'ok' ? null : reply === 'next' ? next : q);
      }
      if (reply === 'ok' || reply === 'next') {
        assert.equal(t.sandbox.sess.question?.question, reply === 'next' ? 'Next?' : undefined);
        if (returnPending && reply === 'ok') {
          t.calls.at(-1).resolve({id: 'a', question: null});
          await new Promise(resolve => setImmediate(resolve));
        }
      } else {
        const state = JSON.parse(t.sandbox.$('s-question').dataset.ownState);
        assert.equal(state.text, 'draft');
        assert.equal(state.pending, false);
        assert.equal(state.error, reply === 'fail' ? 'failure' : 'netRetry');
        const key = t.run('ownQuestionKey(sess.question)');
        t.handlers['question:own-draft']({detail: {id: 'a', key, text: 'edited'}});
        await t.open('b', null); await t.open('a', q);
        assert.equal(JSON.parse(t.sandbox.$('s-question').dataset.ownState).text, 'edited');
        t.handlers['question:own-reset']({detail: {id: 'a', key}});
        assert.equal(t.run('ownAnswerRequests.has("a")'), false);
        assert.equal(t.sandbox.$('s-question').dataset.ownState, 'null');
        const retry = t.start();
        t.calls.at(-1).resolve({ok: true, question: next}); await retry;
        assert.equal(t.sandbox.sess.question.question, 'Next?');
      }
      count++;
    }
  }
  // An old GET and a duplicate submit must not replace or duplicate a POST.
  {
    const t = setup(), oldGet = t.run('loadSession()'), get = t.calls[0];
    const post = t.start();
    const duplicate = await t.start(); assert.equal(duplicate.stale, true);
    assert.equal(t.calls.length, 2);
    t.calls[1].resolve({ok: true, question: next}); await post;
    get.resolve({id: 'a', question: q}); await oldGet;
    assert.equal(t.sandbox.sess.question.question, 'Next?'); count++;
  }
  for (const replacement of [{id: 'a', question: next}, {id: 'a', question: q, perm: {options: []}}]) {
    const t = setup(), post = t.start(); t.sandbox.sess = replacement;
    t.calls[0].resolve({ok: true, question: null}); await post;
    assert.equal(t.sandbox.sess, replacement);
    assert.equal(t.effects.length, 0); count++;
  }
  // A fresh different question retires an old failure and its retained draft.
  {
    const t = setup(), post = t.start();
    t.calls[0].resolve({ok: false, error: 'failure'}); await post;
    const get = t.run('loadSession()'); t.calls.at(-1).resolve({id: 'a', question: next}); await get;
    assert.equal(t.run('ownAnswerRequests.has("a")'), false);
    assert.equal(t.sandbox.sess.question.question, 'Next?'); count++;
  }
  return count;
}
function sheetTransitions() {
  const buttons = ['option', 'send', 'cancel'].map(kind => ({kind, disabled: true, classList: {contains: key => key === 'lab-ask-opt' && kind === 'option'}}));
  const ta = {value: 'draft', readOnly: false, setAttribute() {}};
  const error = {hidden: true};
  const form = {attrs: {}, setAttribute(k, v) {this.attrs[k] = v;}, removeAttribute(k) {delete this.attrs[k];}, querySelector: () => ta};
  const sheet = {setAttribute() {}, querySelectorAll: () => buttons};
  const root = {querySelector: selector => selector === '.lab-ask-own' ? form : selector === 'textarea' ? ta : error};
  const s = {root, sheet, sig: 'a', busy: false, own: true, ownPending: false};
  const sandbox = {S: s, window: {LAB: {is: () => true}}, LAB: {is: () => true},
    $: id => id === 'session-screen' || id === 'sub-screen' ? {hidden: id === 'sub-screen'} : {},
    de: {classList: {remove() {}, toggle() {}}},
    parse: () => sandbox.q, sigOf: value => value.sig, permColor() {},
    sizeOwn() {buttons[1].disabled = s.busy || !ta.value.trim();},
    ownEditor() {assert.equal(s.busy, false); s.own = true;},
    teardown() {s.root = null;},
  };
  const ctx = vm.createContext(sandbox);
  vm.runInContext(section(layers, '  function ownPending(', '  function ownEditor('), ctx);
  vm.runInContext(section(layers, '  function run(){', '  var mo=new MutationObserver(sync);'), ctx);
  sandbox.q = {sig: 'a', pending: true}; vm.runInContext('run()', ctx);
  assert(buttons.every(b => b.disabled)); assert.equal(ta.readOnly, true);
  // A reconstructed pending sheet has no open editor until failure arrives.
  s.own = false;
  sandbox.q = {sig: 'a', pending: false, ownState: {text: 'saved', error: 'failure'}};
  vm.runInContext('run()', ctx);
  assert.equal(ta.value, 'saved'); assert.equal(ta.readOnly, false);
  assert.equal(error.textContent, 'failure'); assert.equal(error.hidden, false);
  assert.equal(buttons[1].disabled, false); assert.equal(buttons[2].disabled, false);
  assert.equal(buttons[0].disabled, false); assert.equal(form.attrs['aria-busy'], undefined);  // options stay pickable with the editor open (13.11)
  // Sync caused by another session's completed promise leaves this editor alone.
  sandbox.q = {sig: 'a', pending: false, ownState: null};
  ta.value = 'unrelated draft'; error.hidden = true;
  vm.runInContext('run()', ctx);
  assert.equal(ta.value, 'unrelated draft'); assert.equal(error.hidden, true);
  assert.equal(buttons[1].disabled, false);
  // Success with no question removes even a busy pending sheet.
  sandbox.q = {sig: 'a', pending: true}; vm.runInContext('run()', ctx);
  sandbox.q = null; vm.runInContext('run()', ctx); assert.equal(s.root, null);
  return 3;
}
(async () => {
  const count = await races() + sheetTransitions();
  console.log(`PASS ${count} browser-free own-answer checks; inline scripts and layers parse`);
})().catch(error => {console.error(error); process.exitCode = 1;});
