/* The on-demand transcript keeps full text as text, pages messages and preserves old-session selection. */
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.hidden = false; this.value = ''; this.textContent = ''; this.isConnected = true; this.parts = {}; }
  append(...nodes) { this.children.push(...nodes); }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  get options() { return this.children; }
  querySelector(selector) { return this.parts[selector] ||= new Element(selector); }
  replaceChildren() { this.children = []; this.textContent = ''; }
  showModal() { this.open = true; }
  addEventListener() {}
}
const button = new Element('button'), body = new Element('body');
let copied;
const long = '<script>doNotRun()</script>' + 'full prompt '.repeat(1000), requests = [];
const row = (id) => ({id, started_at: id, model: 'test', kind: 'codex', outcome: '', tokens: 500, input_tokens: 100,
  output_tokens: 20, cache_read_tokens: 380, cache_write_tokens: 0, usd: 0});
const source = fs.readFileSync(path.join(__dirname, '../src/decompnet/app/static/js/transcripts.js'), 'utf8');
const context = { S: {logFor: '7', slug: 'demo'}, document: {body, createElement: (tag) => new Element(tag)},
  $: () => button, clock: (n) => String(n), beginWork: () => () => {}, esc: (s) => s, toast: (s) => { throw Error(s); },
  copyText: async text => { copied = text; return true; }, money: () => '$0', api: async (url) => {
    requests.push(url); const q = new URL(url, 'http://test').searchParams;
    const id = Number(q.get('session')) || 60, before = Number(q.get('before'));
    const sessions = Array.from({length: 30}, (_, i) => row((before ? 30 : 60) - i));
    return {sessions, session: row(id), events: [{id: q.get('after') === '1' ? 2 : 1, created_at: 1, kind: id === 4 ? 'error' : 'prompt', text: long}], more: q.get('after') !== '1'};
  }, URL, Date };
vm.createContext(context);
vm.runInContext(source.slice(0, source.indexOf('$("helperTranscript").onclick =')), context);
const definition = {name:'project_info', description:'<script>never execute</script>', input_schema:{type:'object', required:['detail'], properties:{detail:{type:'boolean', description:'Show details', default:false}}}};
const catalog = context.transcriptToolCatalog(JSON.stringify([definition]));
assert.equal(catalog.children[0].textContent, '1 tools available to this agent');
const card = catalog.children[1];
assert.equal(card.children[0].children[0].textContent, 'project_info');
assert.equal(card.children[1].textContent, '<script>never execute</script>');
assert.equal(card.children[2].children[0].textContent, 'detail · required');
assert.match(card.children[2].children[1].textContent, /Default: false/);
assert(context.transcriptToolCatalog(JSON.stringify([{type:'function', function:{name:'other', parameters:{}}}])));
assert.equal(context.transcriptToolCatalog('broken JSON'), null);
assert.equal(context.transcriptToolCatalog(JSON.stringify([null])), null);
assert.equal(context.transcriptToolCatalog('{}'), null);

vm.runInContext(source.slice(source.indexOf('$("helperTranscript").onclick =')), context);
(async () => {
  await button.onclick();
  const dialog = body.children[0], events = dialog.querySelector('[data-events]'), select = dialog.querySelector('select');
  assert.equal(events.children[0].children[1].textContent, long);
  assert.equal(events.children[0].open, false);
  await events.children[0].children[2].onclick();
  assert.equal(copied, long);
  assert.equal(events.children[0].children[2].textContent, 'Copied');
  assert(dialog.querySelector('[data-usage]').textContent.includes('380 cache read'));
  await dialog.querySelector('[data-more]').onclick();
  assert.equal(events.children.length, 2);
  assert(requests.at(-1).includes('after=1'));
  await dialog.querySelector('[data-older]').onclick();
  assert.equal(select.options.length, 60);
  select.value = '4'; await select.onchange();
  assert.equal(select.value, '4');
  assert.equal(events.children[0].open, true);
  assert.match(events.children[0].className, /transcript-event-error/);
  assert(requests.at(-1).includes('session=4'));
  console.log('Transcript full-text rendering, usage, paging and old-session selection passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });
