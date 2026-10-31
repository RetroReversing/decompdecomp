"use strict";
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const elements = {diffSum: {}, diff: {querySelectorAll: () => []}, diffProblems: {}};
const esc = text => String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
const ctx = {$: id => elements[id], esc, S: {ov: {kind: 'rom'}}, MARK: {}, MARK_TEXT: {}, insnHtml: esc};
vm.createContext(ctx);
const source = fs.readFileSync('src/decompnet/app/static/js/function.js','utf8');
vm.runInContext(source.slice(source.indexOf('function renderDiff('),source.indexOf('async function loadDiff(')),ctx);
ctx.renderDiff({rows:[[' ','nop','nop']],kind:'matched',score:0,percent:100,problems:[],
  checks:{export:{state:'blocked',ready:false,message:'Non-contiguous pool <needs review>'}}});
assert.match(elements.diffSum.innerHTML,/byte-identical/);
assert.match(elements.diffProblems.innerHTML,/Non-contiguous pool &lt;needs review&gt;/);
ctx.renderDiff({rows:[['i','load','load']],kind:'best',score:1,percent:99.9,problems:[],
  checks:{link:{message:'Resolve <color> before permutations'},export:{state:'unmatched',message:'not matched'}}});
assert.match(elements.diffProblems.innerHTML,/Resolve &lt;color&gt;/);
assert.doesNotMatch(elements.diffProblems.innerHTML,/not matched/);
