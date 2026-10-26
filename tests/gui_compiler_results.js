"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const elements = new Map(), requests = [];
const get = id => {
  if (!elements.has(id)) elements.set(id, { dataset: {}, querySelectorAll: () => [] });
  return elements.get(id);
};
const ctx = { $: get, S: { slug: "demo", fn: "function_one" }, esc: String,
  setHtml: (e, html) => { e.innerHTML = html; }, beginWork: () => () => {},
  api: async (url, options) => requests.push({ url, ...options.body }),
  refreshMap: async () => {}, refreshFn: async () => {}, compilerComparisonBusy: false };
vm.createContext(ctx);
const base = fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js/base.js"), "utf8");
vm.runInContext(base.slice(base.indexOf("function compilerRankedTable(")), ctx);
const source = fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js/function.js"), "utf8");
vm.runInContext(source.slice(source.indexOf("function renderFunctionCompilerComparison("), source.indexOf('$("fnTestCompilers").onclick')), ctx);
const rows = [
  { toolchain: "best", flags: ["-O2"], percent: 90 },
  { toolchain: "chosen", flags: ["-O1", "-g3"], percent: 70 },
  { toolchain: "failed", flags: [], percent: null },
];
ctx.renderFunctionCompilerComparison({ ranked: rows, best: 90, before: 80, tested: 3, exact_matches: 0 }, "demo", "function_one");
assert.match(get("fnCompilerRanked").innerHTML, /data-compiler-choice="2" disabled/);
(async () => {
  await get("fnCompilerRanked").onclick({ target: { closest: () => ({ dataset: { compilerChoice: "1" } }) } });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].toolchain, "chosen");
  assert.deepEqual(Array.from(requests[0].flags), ["-O1", "-g3"]);
  assert.equal(requests[0].allow_non_improving, true);
  ctx.S.fn = "another_function";
  await get("fnCompilerRanked").onclick({ target: { closest: () => ({ dataset: { compilerChoice: "0" } }) } });
  assert.equal(requests.length, 1);
})().catch(e => { console.error(e); process.exitCode = 1; });
