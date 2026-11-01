"use strict";
/* Keep cards and expanded logs mounted while snapshots arrive. */
function renderDeepActivity(progress = {}) {
  const host = $("deepActivity"), slug = S.slug;
  if (host.dataset.slug !== slug) { host.replaceChildren(); host.dataset.slug = slug; }
  const rows = [...(progress.workers || []).map(w => ({...w, active:true})), ...(progress.recent_workers || [])];
  const keep = new Set(rows.map(w => w.function));
  host.querySelectorAll("[data-worker]").forEach(card => { if (!keep.has(card.dataset.worker)) card.remove(); });
  for (const worker of rows) {
    let card = Array.from(host.children).find(c => c.dataset.worker === worker.function);
    if (!card) {
      card = document.createElement("article"); card.className = "deep-worker"; card.dataset.worker = worker.function;
      card.innerHTML = `<header><button class="link-btn mono" data-function type="button"></button><span data-state></span></header><p data-phase></p><div class="deep-worker-metrics" data-metrics></div><progress max="1" value="0" aria-label="Function search budget"></progress><p class="small muted" data-latest></p><details><summary>Recent output</summary><pre class="joblog" data-output></pre></details>`;
      card.querySelector("[data-function]").textContent = worker.function;
      card.querySelector("[data-function]").onclick = () => { if(S.slug === slug) openFn(worker.function); };
      host.append(card);
    }
    card.dataset.active = String(!!worker.active);
    card.querySelector("[data-state]").textContent = worker.active ? "Working" : worker.phase;
    card.querySelector("[data-phase]").textContent = [worker.phase, worker.current_family].filter(Boolean).join(" · ");
    const percent = n => n == null ? "—" : `${Number(n).toFixed(1)}%`;
    card.querySelector("[data-metrics]").textContent = `${percent(worker.starting_percent)} → ${percent(worker.best_percent)} · ${worker.improvements || 0} improvements · ${worker.tested || 0} / ${worker.variant_limit || "—"} tested${worker.active && worker.seconds_left != null ? ` · ${worker.seconds_left}s left` : ""}`;
    const bar = card.querySelector("progress");
    bar.max = worker.variant_limit || 1; bar.value = Math.min(bar.max, worker.tested || 0);
    card.querySelector("[data-latest]").textContent = (worker.lines || []).at(-1) || worker.last_outcome || "Checking the saved baseline…";
    card.querySelector("[data-output]").textContent = (worker.lines || []).join("\n");
  }
  $("deepActivityCount").textContent = `${(progress.workers || []).length} active · ${(progress.recent_workers || []).length} recently finished`;
}
