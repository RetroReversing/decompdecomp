"use strict";
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const elements = new Map(), buttons = [], opened = [];
const get = id => {
  if (!elements.has(id)) elements.set(id, {dataset:{}, innerHTML:"", querySelector:()=>null, querySelectorAll(selector) {
    if (selector === "[data-deep-win]" && this.innerHTML.includes("data-deep-win")) {
      const button = {dataset:{deepWin:"0"}}; buttons.push(button); return [button];
    }
    return [];
  }});
  return elements.get(id);
};
let posts = [], confirmed = true, finished = 0;
const ctx = {$:get, esc:String, openFn:name=>opened.push(name), S:{slug:"game"},
  confirm:()=>confirmed, beginWork:()=>()=>finished++, showJobError:()=>{},
  api:async (url,options)=>{posts.push([url,options.body]);return {};}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync("src/decompnet/app/static/js/rewrite-performance.js","utf8"),ctx);
const source=fs.readFileSync("src/decompnet/app/static/js/steering.js","utf8");
vm.runInContext(source.slice(source.indexOf("function renderDeepSweepResults"),source.indexOf("async function renderDeepSweep()")),ctx);
vm.runInContext(source.slice(source.indexOf('$("deepSweepReset").onclick')),ctx);
ctx.renderDeepSweepResults({recipe_wins:[{function:"func_1234",created_at:1700000000,previous_score:20,new_score:10,provenance:{},family:"typed_temporary"}]});
assert.match(get("deepSweepRecipes").innerHTML, /<th>Date \/ time<\/th>/);
assert.match(get("deepSweepRecipes").innerHTML, /datetime="2023-11-14T22:13:20.000Z"/);
buttons[0].onclick(); assert.deepEqual(opened,["func_1234"]);
(async()=>{
  confirmed=false; await get("deepSweepReset").onclick(); assert.equal(posts.length,0);
  confirmed=true; await get("deepSweepReset").onclick();
  assert.deepEqual(posts,[["/api/permuter/pass",{slug:"game",action:"reset-metrics"}]]);
  assert.equal(finished,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
