/* Regression results stay separate from game progress and developer review states. */
function setupRepairReplay(dialog, scope) {
  const get = selector => dialog.querySelector(selector);
  const start = get('[data-replay-start]'), stop = get('[data-replay-stop]');
  const host = get('[data-replay-work]'), summary = get('[data-replay-summary]'), results = get('[data-replay-results]');
  let job, busy = false, closed = false;
  dialog.addEventListener('close', () => {
    closed = true;
    if (job) api('/api/job/cancel',{method:'POST',body:{id:job}}).catch(() => {});
  }, {once:true});
  async function run(id) {
    if (busy) return;
    busy = true;
    const finish = beginWork(host, 'Testing automatic repairs against captured output', [start, get('[data-replay-trials]'), get('[data-replay-seconds]')]);
    results.textContent = ''; summary.textContent = 'Local compiler tests · no AI tokens · no game source is applied.';
    try {
      job = (await api('/api/repair-examples/replay', {method:'POST', body:{...scope(), ...(id == null ? {} : {id}),
        trials:Number(get('[data-replay-trials]').value), seconds:Number(get('[data-replay-seconds]').value)}})).job;
      if (closed) { await api('/api/job/cancel',{method:'POST',body:{id:job}}); return; }
      stop.hidden = false;
      const report = await followJob(job, host);
      summary.textContent = report.message;
      results.textContent = (report.examples || []).map(row => `#${row.id} ${row.function || ''}\n` +
        (row.skipped ? `Skipped: ${row.skipped}` : Object.entries(row.summary || {}).map(([key,value]) => `${key.replaceAll('_',' ')}: ${value}`).join(' · ')) +
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
