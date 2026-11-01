'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
const element=()=>({value:'',textContent:'',hidden:false,disabled:false,isConnected:true});
const panel={...element(),querySelector(key){if(!elements.has(key))elements.set(key,element());return elements.get(key);}};
let job={state:'idle'},request,fail=false,copied;
const info=()=>({repo:'example/fork',branch:'main',destination:'/tmp/source',git_available:true,job});
const ctx=vm.createContext({document:{createElement:()=>panel},updateInfo:{can_apply:false},
 $:()=>({hidden:false}),esc:x=>x,sleep:async()=>{},copyText:async text=>{copied=text;return true;},
 api:async(url,options)=>{assert.equal(url,'/api/update/checkout');if(options){request=options;if(fail)throw Error('Folder exists');job={state:'cloning',path:options.body.path};return info();}
 if(job.state==='cloning')job={state:'done',path:job.path,commands:'cd /tmp/source\nuv sync --python 3.12\nuv run decomp'};
 return info();}});
const source=fs.readFileSync('src/decompnet/app/static/js/pages.js','utf8');
vm.runInContext(source.slice(source.indexOf('async function sourceCheckoutPanel'),source.indexOf('/* Settings: get decompdecomp from a branch')),ctx);
(async()=>{
 await ctx.sourceCheckoutPanel({append:()=>{}});
 assert.equal(panel.open,true);
 assert.match(elements.get('[data-source-repo]').innerHTML,/example\/fork/);
 elements.get('[data-source-path]').value='/tmp/source';
 await elements.get('[data-source-go]').onclick();
 assert.equal(request.method,'POST');assert.equal(request.body.path,'/tmp/source');
 assert.equal(elements.get('[data-source-next]').hidden,false);
 assert.match(elements.get('[data-source-status]').textContent,/Downloaded to/);
 const button=element();await elements.get('[data-source-copy]').onclick({target:button});
 assert.match(copied,/uv run decomp/);assert.equal(button.textContent,'Copied');
 fail=true;await elements.get('[data-source-go]').onclick();
 assert.equal(elements.get('[data-source-status]').textContent,'Folder exists');
 assert.equal(elements.get('[data-source-go]').disabled,false);
})().catch(e=>{console.error(e);process.exitCode=1;});
