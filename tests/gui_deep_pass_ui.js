"use strict";
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    let html = "", tables = [];
    elements.set(id, {dataset:{}, textContent:"", value:0, hidden:false, disabled:false,
      get innerHTML() { return html; }, set innerHTML(value) {
        html = value; tables = Array.from(value.matchAll(/class="compiler-table"/g), () => ({scrollTop:0, scrollLeft:0}));
      },
      querySelectorAll(selector) { return selector === ".compiler-table" ? tables : []; },
      replaceChildren() { this.innerHTML = ""; this.textContent = ""; },
      append() {},
    });
  }
  return elements.get(id);
}
let follower, finished = 0;
const ctx = {renderDeepActivity:()=>{}, $:element, S:{slug:"game-a"}, esc:String, openFn(){},
  document:{createElement:()=>({})},
  beginWork:()=>()=>finished++, showJobError:()=>{},
  followJob:(job, log, callback, active)=>new Promise(resolve=>{follower={job,callback,active,resolve};}),
  api:async url=>({state:url.includes("game-a")?"running":"idle",job:7,minutes:5,max_variants:100,
    errors:0,completed:0,improvements:[{function:"one",starting_score:10,starting_percent_text:"90%",new_score:5,new_percent_text:"95%"}]}),
};
vm.createContext(ctx);
const source = fs.readFileSync("src/decompnet/app/static/js/steering.js", "utf8");
vm.runInContext(source.slice(source.indexOf("let deepSweepFollowing"), source.indexOf('$("deepSweepStart").onclick')), ctx);
(async()=>{
  await ctx.renderDeepSweep();
  assert.equal(element("deepSweepStop").hidden, false);
  const oldFollower=follower;
  const table=element("deepSweepResults").querySelectorAll(".compiler-table")[0];
  table.scrollTop=177; table.scrollLeft=23;
  ctx.renderDeepSweepResults({improvements:[{function:"one",starting_score:10,starting_percent_text:"90%",new_score:5,new_percent_text:"95%"}]});
  const rebuilt=element("deepSweepResults").querySelectorAll(".compiler-table")[0];
  assert.equal(rebuilt.scrollTop,177); assert.equal(rebuilt.scrollLeft,23);
  ctx.S.slug="game-b";
  const pending=ctx.renderDeepSweep();
  assert.equal(element("deepSweepStop").hidden,true);
  assert.equal(oldFollower.active(),false);
  assert.equal(finished,1);
  await pending;
  assert.equal(element("deepSweepStart").disabled,false);
  assert.equal(element("deepSweepStop").hidden,true);
  oldFollower.callback([], {progress:{function:"wrong-game"}});
  assert.doesNotMatch(element("deepSweepStatus").textContent,/wrong-game/);
  oldFollower.resolve(null);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(finished,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
