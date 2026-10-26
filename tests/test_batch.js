/* Execute the actual batch handler with controlled network completion/failure. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const start = app.indexOf('$("submitBtn").onclick =');
const end = app.indexOf('/* ---------- data loop', start);
assert(start >= 0 && end > start);
const handler = app.slice(start, end);
const viewer = fs.readFileSync(path.join(root, 'static/viewer.html'), 'utf8');
assert(viewer.indexOf('<dialog id="stage"') > viewer.indexOf('</main>'),
       'confirmation must remain outside the hideable sidebar');

class Element {
  constructor() { this.children = []; this.attrs = {}; this.hidden = false; this.disabled = false; }
  appendChild(child) { this.children.push(child); }
  setAttribute(key, val) { this.attrs[key] = val; }
  removeAttribute(key) { delete this.attrs[key]; }
  showModal() { this.open = true; }
  close() { this.open = false; }
}
function fixture(threads, response) {
  const els = { submitBtn: new Element(), stage: new Element(), stageError: new Element() };
  const calls = [], toasts = [];
  let refreshes = 0;
  const context = {
    $: id => els[id], THREADS: threads, docid: 'synthetic',
    document: { createElement: () => new Element() },
    esc: s => String(s).replaceAll('<', '&lt;'), oneLine: t => t.text,
    api: async (...args) => { calls.push(args); return response(); },
    toast: text => toasts.push(text), refresh: async () => { refreshes++; },
  };
  vm.runInNewContext(handler, context);
  return { els, calls, toasts, refreshes: () => refreshes,
    open() { els.submitBtn.onclick(); return els.stage.children.at(-1)?.children; } };
}
const draft = { id: 'c-0001', status: 'draft', kind: 'doc', text: 'Synthetic question' };
(async () => {
  const empty = fixture([], () => { throw Error('must not send'); });
  empty.open();
  assert(!empty.els.stage.open);
  const privateOnly = fixture([{...draft, kind:'note'}], () => {});
  privateOnly.open();
  assert(!privateOnly.els.stage.open, 'private notes never enter batch confirmation');

  const threads = [draft, { id:'c-0002', status:'answered', kind:'doc', text:'Delivered',
    turns:[{kind:'followup', draft:true, text:'Synthetic follow-up'}] }];
  let finish;
  const success = fixture(threads, () => new Promise(resolve => { finish = resolve; }));
  const [submit, cancel] = success.open();
  assert(success.els.stage.open);
  assert(success.els.stage.innerHTML.includes('Submit 2 items?'));
  assert(success.els.stage.innerHTML.includes('Synthetic follow-up'));
  const pending = submit.onclick();
  assert(submit.disabled && cancel.disabled);
  assert.equal(success.els.stage.attrs['aria-busy'], 'true');
  let prevented = false;
  success.els.stage.oncancel({preventDefault() { prevented = true; }});
  assert(prevented, 'Escape must not dismiss an in-flight submission');
  await submit.onclick();
  assert.equal(success.calls.length, 1, 'double click must not send twice');
  finish({ok:true, batch:1, ids:['c-0001'], fids:['f-0001']});
  await pending;
  assert(!success.els.stage.open);
  assert(!submit.disabled && !cancel.disabled);
  assert.equal(success.refreshes(), 1);
  assert.equal(success.calls[0][0], '/api/submit');
  assert(success.toasts[0].includes('delivered to the agent'));

  for (const fail of [() => { throw Error('Network unavailable'); },
                      () => ({ok:false, err:'Synthetic server error'})]) {
    let attempt = 0;
    const retry = fixture([draft], () => ++attempt === 1 ? fail() : {ok:true,batch:2,ids:['c-0001']});
    const [ok] = retry.open();
    const list = retry.els.stage.innerHTML;
    await ok.onclick();
    assert(retry.els.stage.open, 'failure must retain confirmation');
    assert.equal(retry.els.stage.innerHTML, list);
    assert(!retry.els.stageError.hidden);
    assert(retry.els.stageError.textContent.startsWith('Submission incomplete:'));
    assert(!ok.disabled);
    assert.equal(retry.refreshes(), 0);
    await ok.onclick();
    assert(!retry.els.stage.open);
    assert.equal(retry.calls.length, 2);
  }
  const cancelled = fixture([draft], () => { throw Error('must not send'); });
  const buttons = cancelled.open();
  assert(cancelled.els.stage.innerHTML.includes('Submit 1 item?'));
  buttons[1].onclick();
  assert(!cancelled.els.stage.open);
  assert.equal(cancelled.calls.length, 0);
  console.log('batch confirmation: ALL GREEN');
})().catch(error => { console.error(error); process.exitCode = 1; });
