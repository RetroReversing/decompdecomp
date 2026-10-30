/* Evidence stays plain text; filtering, review and source opt-in export share the chosen scope. */
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const requests = [], downloads = [], copied = [], notices = [];
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.hidden = false; this.value = ''; this.textContent = ''; this.isConnected = true; this.parts = {}; this.listeners = {}; }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  append(...nodes) { this.children.push(...nodes); }
  get options() { return this.children; }
  querySelector(selector) { return this.parts[selector] ||= new Element(selector); }
  replaceChildren() { this.children = []; }
  setAttribute() {}
  showModal() { this.open = true; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  click() { downloads.push(this.download); }
  remove() { this.isConnected = false; }
}
const body = new Element('body'), button = new Element('button');
let state = 'new', qualityPending = true, confirmed = false;
const row = () => ({id: 42, project: '<b>demo</b>', function: 'target', category: 'types', status: state});
const context = { S: {slug: 'demo'}, document: {body, createElement: (tag) => new Element(tag)}, $: () => button,
  beginWork: () => () => {}, toast: (message, kind) => { if (kind === 'err') throw Error(message); notices.push(message); }, esc: (s) => s,
  confirm: () => confirmed,
  copyText: async (value) => copied.push(await value),
  followJob: async () => ({message: 'Opened terminal', repository: '/private/repository'}),
  setTimeout: (fn) => fn(), URL: {createObjectURL: () => 'blob:local', revokeObjectURL: () => {}},
  fetch: async (url) => { requests.push({url}); return {ok: true, blob: async () => ({})}; },
  api: async (url, opts) => {
    requests.push({url, opts});
    if (url.includes('/recovery')) return {diff:'- old\n+ <script>textOnly()</script>',source:'int candidate(){}',target:'target assembly',measurement:null};
    if (url.includes('/quality')) {
      if (opts?.method === 'POST') { qualityPending = false; return {resumed: 'suspect'}; }
      if (url.includes('function=')) return {evidence: 'Compiled alternative, quality unverified'};
      return {reports: qualityPending ? [{function:'suspect', slug:'demo', source_hash:'hash', warning:'Reported uninitialized value', alternatives:[123]}] : []};
    }
    if (url.includes('/investigate')) return opts?.method === 'POST' ? {job: 'j1'} :
      {prompt: 'Investigate these examples safely.', agents: [{id: 'codex', name: 'Codex CLI', installed: true}]};
    if (opts?.method === 'POST') { state = opts.body.status; return {message: 'Saved'}; }
    const query = new URL(url, 'http://test').searchParams;
    if (query.get('id')) return {...row(), explanation: 'Changed a type.', automation: 'Try signedness.',
      evidence: {source_change_label: 'C tokens changed', diff: '- old\n+ <script>neverExecute()</script>', after: {source: '<script>neverExecute()</script>'}}, review_note: ''};
    return {examples: [row()], categories: ['unclassified', 'types'], states: ['new', 'reviewed'],
      groups: [{category: 'types', status: state, count: 1}], more: false};
  }};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/decompnet/app/static/js/candidate-recovery.js'), 'utf8'), context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/decompnet/app/static/js/repair-feedback.js'), 'utf8'), context);
(async () => {
  await button.onclick();
  const dialog = body.children[0], list = dialog.querySelector('[data-list]');
  const quality = dialog.querySelector('[data-quality]');
  assert.equal(quality.children.length, 1);
  const reportBody = quality.children[0].children[1];
  await reportBody.children[2].onclick();
  assert(reportBody.children[4].textContent.includes('quality unverified'));
  const comparison = reportBody.children[6];
  comparison.open = true; await comparison.listeners.toggle();
  assert(comparison.children[2].textContent.includes('<script>textOnly()</script>'));
  assert(comparison.children[0].textContent.includes('recheck needed'));
  const choose = reportBody.children[5];
  assert(choose.textContent.includes('#123'));
  await choose.onclick();
  assert(!requests.some(r => r.opts?.body.action === 'adopt'));
  confirmed = true;
  await choose.onclick();
  const adopted = requests.find(r => r.opts?.body.action === 'adopt');
  assert.equal(adopted.opts.body.attempt_id, 123);
  assert.equal(adopted.opts.body.source_hash, 'hash');
  qualityPending = true;
  await dialog.querySelector('[data-refresh]').onclick();
  await quality.children[0].children[1].children[3].onclick();
  const reviewed = requests.find(r => r.opts?.body.source_hash === 'hash');
  assert.equal(reviewed.opts.body.function, 'suspect'); assert.equal(reviewed.opts.body.slug, 'demo');
  assert.equal(quality.children.length, 0);
  assert(requests[0].url.includes('comparable=1'));
  assert(requests[0].url.includes('status=new'));
  const statusFilter = dialog.querySelector('[data-status]'); statusFilter.value = ''; await statusFilter.onchange();
  assert(requests.filter(r => !r.url.includes('/quality')).at(-1).url.includes('status='));
  const details = list.children[0]; details.open = true; await details.listeners.toggle();
  const evidence = details.children[1], evidencePanel = evidence.children[5];
  const pre = evidencePanel.children.find((child) => child.tag === 'pre');
  assert(evidencePanel.children.some((child) => child.tag === 'p' && child.textContent === 'C tokens changed'));
  assert(pre.children.some((line) => line.textContent.includes('<script>neverExecute()</script>')));
  assert(pre.children.some((line) => line.className === 'diff-added'));
  assert(dialog.className.includes('repair-dialog'));
  assert(details.className === 'repair-card');
  assert(details.children[0].children[1].textContent.includes('<b>demo</b>'));
  const advanced = evidence.children[6];
  const review = advanced.children[1], kind = advanced.children[2], note = advanced.children[3], save = advanced.children[4];
  const send = evidence.children[2]; await send.onclick();
  const handoff = requests.find((item) => item.opts?.body.agent === 'codex');
  assert.equal(handoff.opts.body.id, 42); assert.equal(handoff.opts.body.slug, 'demo');
  assert(evidence.children[4].textContent.includes('/private/repository'));
  review.value = 'reviewed'; kind.value = 'types'; note.value = 'Add an AST rule'; await save.onclick();
  const posted = requests.find((item) => item.opts?.body.status === 'reviewed');
  assert.equal(posted.opts.body.note, 'Add an AST rule'); assert.equal(posted.opts.body.status, 'reviewed');
  const all = dialog.querySelector('[data-all]'); all.checked = true; await all.onchange();
  assert(requests.filter(r => !r.url.includes('/quality')).at(-1).url.includes('all=1'));
  const summary = dialog.querySelector('[data-export]'); await summary.onclick({currentTarget: summary});
  assert(requests.at(-1).url.includes('include_source=0')); assert.equal(downloads.at(-1), 'repair-examples-summary.json');
  const full = dialog.querySelector('[data-export-full]'); await full.onclick({currentTarget: full});
  assert(requests.at(-1).url.includes('include_source=1')); assert.equal(downloads.at(-1), 'repair-examples-full.json');
  await dialog.querySelector('[data-prompt]').onclick();
  assert.equal(copied[0], 'Investigate these examples safely.');
  console.log('Repair evidence rendering, review, filtering and explicit source export passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });
