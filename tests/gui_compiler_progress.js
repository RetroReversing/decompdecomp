"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const read = (name) => fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js", name + ".js"), "utf8");
const fields = new Map();
const box = { hidden: true, dataset: {}, querySelector(key) { if (!fields.has(key)) fields.set(key, {}); return fields.get(key); } };
const ctx = { $: () => box };
vm.createContext(ctx);
vm.runInContext(read("compiler-progress"), ctx);
ctx.renderCompilerProgress(null);
assert.equal(fields.get("[data-best]").textContent, "—");
ctx.renderCompilerProgress({ function: "<synthetic>", tested: 80, combinations: 196, before: 99.72, best: 99.94,
  completed: 3, improved: 2, exact: 1, profile: ["Example GCC", ["-O2", "-g3"]] });
assert.equal(fields.get("[data-current]").textContent, "<synthetic>");
assert.equal(fields.get("[data-best]").textContent, "99.94%");
assert.equal(fields.get("[data-bar]").value, 80);
assert.equal(fields.get("[data-bar]").max, 196);
assert.equal(box.dataset.improved, "true");
ctx.renderCompilerProgress(null, { tried: 4, skipped: 2, improvements: [{}, {}], confirmed: 1 });
assert.equal(fields.get("[data-current]").textContent, "Comparison finished");
assert.equal(fields.get("[data-completed]").textContent, 4);
assert.ok(fields.get("[data-profile]").textContent.includes("Nothing has been applied"));
ctx.renderCompilerProgress(null);
assert.equal(box.dataset.improved, "false");

async function checkScroll() {
  const base = read("base");
  for (const mode of ["paused", "bottom", "explicit-off", "unchanged"]) {
    let writes = 0, text = mode === "unchanged" ? "old\nnew" : "old";
    const log = { scrollTop: mode === "paused" ? 25 : 200, clientHeight: 100, scrollHeight: 300,
      _followTail: mode !== "explicit-off", get textContent() { return text; },
      set textContent(value) { writes++; text = value; this.scrollTop = 0; this.scrollHeight = 400; } };
    const top = log.scrollTop;
    const snapshot = { state: "done", log: ["old", "new"], result: {}, progress: { tested: 2 } };
    let delivered;
    const context = { api: async () => snapshot, sleep: async () => {} };
    vm.createContext(context);
    vm.runInContext(base.slice(base.indexOf("const JOB_POLL_MS"), base.indexOf("/* The map's still")), context);
    await context.followJob("test", log, (_lines, job) => { delivered = job.progress.tested; });
    assert.equal(delivered, 2);
    assert.equal(writes, mode === "unchanged" ? 0 : 1);
    assert.equal(log.scrollTop, mode === "bottom" ? 400 : top);
  }
}
checkScroll().catch((error) => { console.error(error); process.exitCode = 1; });
