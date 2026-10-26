/* Evidence stays plain text; filtering, review and source opt-in export share the chosen scope. */
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const requests = [], downloads = [];
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
let state = 'new';
const row = () => ({id: 42, project: '<b>demo</b>', function: 'target', category: 'types', status: state});
const context = { S: {slug: 'demo'}, document: {body, createElement: (tag) => new Element(tag)}, $: () => button,
  beginWork: () => () => {}, toast: (message) => { throw Error(message); }, esc: (s) => s,
  setTimeout: (fn) => fn(), URL: {createObjectURL: () => 'blob:local', revokeObjectURL: () => {}},
  fetch: async (url) => { requests.push({url}); return {ok: true, blob: async () => ({})}; },
  api: async (url, opts) => {
    requests.push({url, opts});
    if (opts?.method === 'POST') { state = opts.body.status; return {message: 'Saved'}; }
    const query = new URL(url, 'http://test').searchParams;
    if (query.get('id')) return {...row(), explanation: 'Changed a type.', automation: 'Try signedness.',
      evidence: {after: {source: '<script>neverExecute()</script>'}}, review_note: ''};
    return {examples: [row()], categories: ['unclassified', 'types'], states: ['new', 'reviewed'],
      groups: [{category: 'types', status: state, count: 1}], more: false};
  }};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/decompnet/app/static/js/repair-feedback.js'), 'utf8'), context);
(async () => {
  await button.onclick();
  const dialog = body.children[0], list = dialog.querySelector('[data-list]');
  assert(requests[0].url.includes('comparable=1'));
  const details = list.children[0]; details.open = true; await details.listeners.toggle();
  const evidence = details.children[1], pre = evidence.children.at(-1);
  assert(pre.textContent.includes('<script>neverExecute()</script>'));
  const review = evidence.children[1], kind = evidence.children[2], note = evidence.children[3], save = evidence.children[4];
  review.value = 'reviewed'; kind.value = 'types'; note.value = 'Add an AST rule'; await save.onclick();
  const posted = requests.find((item) => item.opts?.method === 'POST');
  assert.equal(posted.opts.body.note, 'Add an AST rule'); assert.equal(posted.opts.body.status, 'reviewed');
  const all = dialog.querySelector('[data-all]'); all.checked = true; await all.onchange();
  assert(requests.at(-1).url.includes('all=1'));
  const summary = dialog.querySelector('[data-export]'); await summary.onclick({currentTarget: summary});
  assert(requests.at(-1).url.includes('include_source=0')); assert.equal(downloads.at(-1), 'repair-examples-summary.json');
  const full = dialog.querySelector('[data-export-full]'); await full.onclick({currentTarget: full});
  assert(requests.at(-1).url.includes('include_source=1')); assert.equal(downloads.at(-1), 'repair-examples-full.json');
  console.log('Repair evidence rendering, review, filtering and explicit source export passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });
