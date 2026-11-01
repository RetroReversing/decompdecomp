/* Regression results stay separate from game progress and developer review states. */
function setupRepairReplay(dialog, scope) {
  const get = selector => dialog.querySelector(selector);
  const start = get('[data-replay-start]'), stop = get('[data-replay-stop]');
  const host = get('[data-replay-work]'), summary = get('[data-replay-summary]'), results = get('[data-replay-results]');
  let lastReport;
  get('[data-replay-save]').onclick = () => {
    if (!lastReport) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(lastReport, null, 2)], {type:"application/json"}));
    const link = document.createElement("a"); link.href = url; link.download = "repair-analysis.json";
    document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  let job, busy = false, closed = false;
  dialog.addEventListener('close', () => {
    closed = true;
    if (job) api('/api/job/cancel',{method:'POST',body:{id:job}}).catch(() => {});
  }, {once:true});
  async function run(id) {
    if (busy) return;
    busy = true; lastReport = null; get('[data-replay-save]').hidden = true;
    const finish = beginWork(host, 'Testing automatic repairs against captured output', [start, get('[data-replay-trials]'), get('[data-replay-seconds]'), get('[data-replay-minimize]'), get('[data-replay-diagnostics]')]);
    results.textContent = ''; summary.textContent = 'Local compiler tests · no AI tokens · no game source is applied.';
    try {
      job = (await api('/api/repair-examples/replay', {method:'POST', body:{...scope(), ...(id == null ? {} : {id}),
        trials:Number(get('[data-replay-trials]').value), seconds:Number(get('[data-replay-seconds]').value), minimize:get('[data-replay-minimize]').checked, diagnostics:get('[data-replay-diagnostics]').checked}})).job;
      if (closed) { await api('/api/job/cancel',{method:'POST',body:{id:job}}); return; }
      stop.hidden = false;
      const report = await followJob(job, host);
      lastReport = report; get('[data-replay-save]').hidden = false;
      summary.textContent = report.message;
      results.textContent = (report.examples || []).map(row => `#${row.id} ${row.function || ''}\n` +
        (row.skipped ? `Skipped: ${row.skipped}` : Object.entries(row.summary || {}).map(([key,value]) => `${key.replaceAll('_',' ')}: ${value}`).join(' · ')) +
        (row.minimization ? `\nEdit minimization: ${row.minimization.reverted} of ${row.minimization.edits} edits reverted; ${row.minimization.retained.length} retained${row.minimization.exhausted ? ' (budget reached)' : ''}. Compiler-output evidence only.` : '') +
        (row.compiler_diagnostics ? `\nCompiler phases: ${row.compiler_diagnostics.skipped || (row.compiler_diagnostics.output_unchanged ? 'output unchanged; ' : 'output changed; phase evidence is not comparable; ') + Object.keys(row.compiler_diagnostics.phases || {}).join(', ')}` : '') +
        (row.remaining ? `\n${row.remaining} candidates remain outside this budget.` : '')).join('\n\n');
      results.hidden = false;
    } catch (error) { showJobError(host,error.message); summary.textContent = 'Replay ended. No game progress or review states changed.'; }
    finally { job = null; busy = false; stop.hidden = true; finish(); }
  }
  start.onclick = () => run();
  stop.onclick = async () => {
    try { if (job) await api('/api/job/cancel',{method:'POST',body:{id:job}}); }
    catch (error) { showJobError(host,error.message); }
  };
  return run;
}
