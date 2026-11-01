'use strict';
const vm=require('node:vm'), fs=require('node:fs'), assert=require('node:assert/strict');
function element(){return {dataset:{},children:[],fields:new Map(),textContent:'',innerHTML:'',
 querySelector(key){if(!this.fields.has(key))this.fields.set(key,element());return this.fields.get(key);},
 querySelectorAll(){return this.children;},replaceChildren(){this.children=[];},append(child){this.children.push(child);child.remove=()=>{this.children=this.children.filter(c=>c!==child);};}};}
const host=element(), count=element(), opened=[];
const ctx=vm.createContext({$:id=>id==='deepActivity'?host:count,S:{slug:'a'},document:{createElement:element},openFn:name=>opened.push(name)});
vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/deep-activity.js','utf8'),ctx);
const worker=(name,lines)=>({function:name,phase:'random rewrites',tested:4,variant_limit:10,starting_percent:90,best_percent:95,improvements:1,lines});
ctx.renderDeepActivity({workers:[worker('<a>',['a only']),worker('b',['b only'])]});
assert.equal(host.children.length,2);
const card=host.children[0], details=card.querySelector('details'); details.open=true;
assert.equal(card.querySelector('[data-function]').textContent,'<a>');
assert.equal(card.querySelector('[data-output]').textContent,'a only');
ctx.renderDeepActivity({workers:[worker('<a>',['new a'])],recent_workers:[worker('b',['finished b'])]});
assert.equal(host.children[0],card);assert.equal(details.open,true);
assert.equal(card.querySelector('[data-output]').textContent,'new a');
card.querySelector('[data-function]').onclick();assert.deepEqual(opened,['<a>']);
assert.match(count.textContent,/1 active/);
ctx.S.slug='other';ctx.renderDeepActivity({});assert.equal(host.children.length,0);
card.querySelector('[data-function]').onclick();assert.equal(opened.length,1);
