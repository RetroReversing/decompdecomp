/* A stable summary outside the raw log: no focus changes or scrolling as results arrive. */
function renderCompilerProgress(progress, result) {
  const box = $("compilerProgress");
  box.hidden = false;
  const set = (key, text) => { box.querySelector(`[data-${key}]`).textContent = text; };
  if (!progress && !result) {
    box.dataset.improved = "false";
    set("current", "Preparing…"); set("tested", "Waiting for the first result");
    set("before", "—"); set("best", "—"); set("completed", "0"); set("improved", "0");
    set("exact", "0 functions with an exact match");
    set("profile", "Comparing the saved C. Compiler settings stay unchanged.");
    const bar = box.querySelector("[data-bar]"); bar.max = 1; bar.value = 0;
    return;
  }
  if (result) {
    box.dataset.improved = "false";
    set("current", "Comparison finished");
    set("tested", `${result.tried} tested · ${result.skipped || 0} unchanged comparisons skipped`);
    set("completed", result.tried); set("improved", (result.improvements || []).length);
    set("exact", `${result.confirmed || 0} functions with an exact match`);
    set("before", "—"); set("best", "—");
    set("profile", "Review the better settings below and choose Apply to function. Nothing has been applied automatically.");
    const bar = box.querySelector("[data-bar]"); bar.max = 1; bar.value = 1;
    return;
  }
  set("current", progress.function);
  set("tested", `${progress.tested} / ${progress.combinations} compiler and flag combinations`);
  set("before", progress.before == null ? "—" : `${progress.before.toFixed(2)}%`);
  set("best", progress.best == null ? "—" : `${progress.best.toFixed(2)}%`);
  set("completed", progress.completed); set("improved", progress.improved);
  set("exact", `${progress.exact} functions with an exact match`);
  box.dataset.improved = progress.best != null && (progress.before == null || progress.best > progress.before + .001) ? "true" : "false";
  set("profile", progress.profile ? `Best settings: ${progress.profile[0]} ${progress.profile[1].join(" ")} · not applied` : "Comparing the saved C. Compiler settings stay unchanged.");
  const bar = box.querySelector("[data-bar]"); bar.max = progress.combinations || 1; bar.value = progress.tested;
}
