/* Captured match evidence and proposed rules stay local until the user downloads them. */
$("repairExamples").onclick = async () => {
  const slug = S.slug, dialog = document.createElement("dialog");
  dialog.className = "transcript-dialog repair-dialog";
  dialog.setAttribute('aria-labelledby', 'repair-dialog-title');
  dialog.innerHTML = `<header class="repair-header"><div><span class="repair-eyebrow">Improve automatic matching</span>
    <h2 id="repair-dialog-title">Repair examples</h2><p>Review what changed when a function matched. Turn useful patterns into automatic repairs.</p></div>
    <button data-close type="button" aria-label="Close repair examples">Close</button></header>
    <div class="repair-scroll"><div class="repair-filters">
    <div class="repair-filter-selects"><label>Category<select data-category aria-label="Repair category"><option value="">All categories</option></select></label>
    <label>Review state<select data-status aria-label="Review state"><option value="new" selected>New (not yet reviewed)</option><option value="">All review states</option></select></label>
    <button data-refresh type="button">Refresh</button></div>
    <div class="repair-filter-checks"><label><input type="checkbox" data-all> Across all projects</label>
    <label><input type="checkbox" data-comparable checked> Only with changed failed C</label></div></div>
    <section data-quality class="repair-list" aria-label="Candidate quality review"></section>
    <p data-count class="repair-count" role="status"></p><div data-list class="repair-list"></div>
    <button data-more hidden type="button">Older examples</button>
    <details class="repair-help"><summary>How to use these examples</summary><p>Expand a card to compare the failed candidate with the match.
    Send promising examples to a coding agent, or export them for analysis. Review and test any proposed app changes: examples do not teach the permuter automatically.</p>
    <p class="muted small">Captures prefer comparable compiler settings. Headers, compiler changes and other edits may also matter; the diff alone does not prove the cause.</p></details></div>
    <footer class="repair-footer"><div class="repair-export-actions"><button data-export-full type="button">Export full examples</button>
    <button data-export type="button">Export summaries</button><button data-prompt type="button">Copy analysis prompt</button></div>
    <p class="muted small">Full examples include game source and headers. Downloads stay local until you share them. Review notes may also contain code.</p></footer>`;
  document.body.append(dialog);
  const get = (selector) => dialog.querySelector(selector);
  const list = get('[data-list]'), category = get('[data-category]'), status = get('[data-status]'), all = get('[data-all]');
  const comparable = get('[data-comparable]');
  comparable.checked = true; status.value = 'new';
  let before = 0, investigation;
  const investigationConfig = () => investigation ||= api('/api/repair-examples/investigate');
  const scope = () => `slug=${encodeURIComponent(slug)}&all=${all.checked ? 1 : 0}`;
  const filters = () => `${scope()}&comparable=${comparable.checked ? 1 : 0}&category=${encodeURIComponent(category.value)}&status=${encodeURIComponent(status.value)}`;
  const controls = [category, status, all, comparable, get('[data-refresh]'), get('[data-more]')];
  function options(select, values) {
    for (const value of values) { if (Array.from(select.options).some((option) => option.value === value)) continue; const option = document.createElement('option'); option.value = value; option.textContent = value.replaceAll('_', ' '); select.append(option); }
  }
  function card(row, states, categories) {
    const details = document.createElement('details'), summary = document.createElement('summary');
    details.className = 'repair-card';
    const title = document.createElement('span'); title.className = 'repair-card-title'; title.textContent = row.function;
    const meta = document.createElement('span'); meta.className = 'repair-card-meta'; meta.textContent = `#${row.id} · ${row.project}${row.agent ? ' · ' + row.agent : ''}`;
    const badges = document.createElement('span'); badges.className = 'repair-badges';
    for (const value of [row.category.replaceAll('_', ' '), row.status, row.evidence_kind || 'source repair', ...(row.automation_known ? ['already automated'] : [])]) {
      const badge = document.createElement('span'); badge.className = 'repair-badge'; badge.textContent = value; badges.append(badge);
    }
    summary.append(title, meta, badges);
    const body = document.createElement('div'); body.className = 'repair-card-body'; details.append(summary, body);
    details.addEventListener('toggle', async () => {
      if (!details.open || details.loaded) return;
      const finish = beginWork(body, 'Loading repair evidence');
      try {
        const data = await api(`/api/repair-examples?${scope()}&id=${row.id}`);
        const explanation = document.createElement('p');
        explanation.textContent = `${data.explanation || 'No agent explanation supplied.'}${data.automation ? ' Suggested automation: ' + data.automation : ''}`;
        const pre = document.createElement('pre'); pre.className = 'repair-diff'; pre.setAttribute('tabindex', '0'); pre.setAttribute('aria-label', 'Source changes');
        const change = document.createElement('p'); change.className = 'muted small';
        change.textContent = data.evidence.source_change_label || '';
        const diff = data.evidence.diff || 'No comparable failed source is available. Check the full technical evidence for settings and context.';
        for (const line of diff.split('\n')) {
          const span = document.createElement('span');
          span.className = line.startsWith('@@') ? 'diff-range' : line.startsWith('+') ? 'diff-added' : line.startsWith('-') ? 'diff-removed' : 'diff-context';
          span.textContent = line; pre.append(span);
        }
        const raw = document.createElement('details'), rawTitle = document.createElement('summary'), rawText = document.createElement('pre');
        rawTitle.textContent = 'Full technical evidence'; rawText.textContent = JSON.stringify(data.evidence, null, 2);
        raw.append(rawTitle, rawText);
        const review = document.createElement('select'); review.setAttribute('aria-label', `Review state for example ${row.id}`);
        for (const value of states) { const option = document.createElement('option'); option.value = value; option.textContent = value; review.append(option); }
        review.value = data.status;
        const kind = document.createElement('select'); kind.setAttribute('aria-label', `Category for example ${row.id}`);
        for (const value of categories) { const option = document.createElement('option'); option.value = value; option.textContent = value.replaceAll('_', ' '); kind.append(option); }
        kind.value = data.category;
        const note = document.createElement('textarea'); note.setAttribute('aria-label', `Your review note for example ${row.id}`);
        note.placeholder = 'Your review note, proposed rule or implementation PR'; note.value = data.review_note || '';
        const save = document.createElement('button'); save.type = 'button'; save.textContent = 'Save review';
        save.onclick = async () => {
          const done = beginWork(body, 'Saving review', [review, kind, note, save]);
          try { await api('/api/repair-examples', {method: 'POST', body: {slug, all: all.checked, id: row.id, status: review.value, category: kind.value, note: note.value}}); await load(); }
          catch (error) { toast(esc(error.message), 'err'); }
          finally { done(); }
        };
        const providers = document.createElement('select');
        providers.className = 'repair-provider';
        providers.setAttribute('aria-label', `Coding agent for example ${row.id}`);
        const config = await investigationConfig();
        for (const agent of config.agents) {
          const option = document.createElement('option'); option.value = agent.id;
          option.textContent = agent.name + (agent.installed ? '' : ' — not installed'); option.disabled = !agent.installed;
          providers.append(option);
        }
        const installed = config.agents.find((agent) => agent.installed);
        if (installed) providers.value = installed.id;
        const send = document.createElement('button'); send.type = 'button'; send.textContent = 'Investigate with agent'; send.className = 'repair-investigate';
        send.disabled = !installed;
        const hint = document.createElement('p'); hint.className = 'muted small';
        hint.textContent = 'Opens a terminal with a fresh decompdecomp checkout and this example, including game source and headers. '
          + 'Uses the CLI’s configured model/account and may consume allowance. Follow or stop it in that terminal; it is separate from game agents. '
          + 'No supported CLI installed? Export the evidence and copy the prompt.';
        const log = document.createElement('pre'); log.hidden = true;
        send.onclick = async () => {
          log.hidden = false;
          const done = beginWork(body, 'Preparing repository investigation', [send, providers]);
          try {
            const started = await api('/api/repair-examples/investigate', {method: 'POST',
              body: {slug, all: all.checked, id: row.id, agent: providers.value}});
            const result = await followJob(started.job, log);
            log.textContent = `${result.message}\nApp checkout: ${result.repository}`;
          } catch (error) { log.textContent = error.message; toast(esc(error.message), 'err'); }
          finally { done(); }
        };
        const advanced = document.createElement('details'), advancedTitle = document.createElement('summary');
        advanced.className = 'repair-review';
        advancedTitle.textContent = 'Classify and save review'; advanced.append(advancedTitle, review, kind, note, save);
        const evidence = document.createElement('details'), evidenceTitle = document.createElement('summary');
        evidenceTitle.textContent = 'Before/after evidence and compiler settings'; evidence.append(evidenceTitle, change, pre, raw); evidence.open = true;
        body.append(explanation, providers, send, hint, log, evidence, advanced); details.loaded = true;
      } catch (error) { toast(esc(error.message), 'err'); }
      finally { finish(); }
    });
    return details;
  }
  async function load(reset = true) {
    const done = beginWork(list, 'Loading repair examples', controls);
    try {
      const data = await api(`/api/repair-examples?${filters()}&before=${reset ? 0 : before}`);
      if (!dialog.isConnected) return;
      if (reset) {
        list.replaceChildren();
        const quality = await api(`/api/repair-examples/quality?${scope()}`);
        const area = get('[data-quality]'); area.replaceChildren();
        for (const report of quality.reports || []) {
          const panel = document.createElement('details'); panel.className = 'repair-card';
          const heading = document.createElement('summary');
          heading.textContent = `${report.function} · ${report.slug}${report.issue ? ' · ' + report.issue.replaceAll('_', ' ') : ''} · automatic work paused`;
          const body = document.createElement('div'); body.className = 'repair-card-body';
          const warning = document.createElement('p'); warning.textContent = report.warning;
          const hint = document.createElement('p'); hint.className = 'muted small';
          hint.textContent = 'This is an agent concern, not proof. Review the saved alternatives and target before resuming. Scores and match status have not changed.';
          const evidence = document.createElement('pre'); evidence.hidden = true;
          const inspect = document.createElement('button'); inspect.textContent = 'Show quality evidence';
          inspect.onclick = async () => {
            const finish = beginWork(body, 'Loading candidate evidence', [inspect]);
            try { evidence.textContent = (await api(`/api/repair-examples/quality?slug=${encodeURIComponent(report.slug)}&function=${encodeURIComponent(report.function)}`)).evidence; evidence.hidden = false; }
            catch (error) { toast(esc(error.message), 'err'); } finally { finish(); }
          };
          const resume = document.createElement('button'); resume.textContent = 'Reviewed: resume this candidate';
          resume.onclick = async () => {
            const finish = beginWork(body, 'Saving candidate review', [resume]);
            try { await api('/api/repair-examples/quality', {method:'POST', body:{slug:report.slug, function:report.function, source_hash:report.source_hash}}); await load(); }
            catch (error) { toast(esc(error.message), 'err'); } finally { finish(); }
          };
          body.append(warning, hint, inspect, resume, evidence); panel.append(heading, body); area.append(panel);
        }
      }
      options(category, data.categories); options(status, data.states);
      get('[data-count]').textContent = data.groups.map((group) => `${group.count} ${group.category.replaceAll('_', ' ')} (${group.status})`).join(' · ') || 'No examples for these filters.';
      if (reset && !data.examples.length) { const empty = document.createElement('p'); empty.className = 'repair-empty'; empty.textContent = 'No examples match these filters. Try another review state or clear “Only with changed failed C” to include baseline and settings-only captures.'; list.append(empty); }
      for (const row of data.examples) { list.append(card(row, data.states, data.categories)); before = row.id; }
      get('[data-more]').hidden = !data.more;
    } catch (error) { toast(esc(error.message), 'err'); }
    finally { done(); }
  }
  async function download(full, button) {
    const done = beginWork(list, 'Preparing local export', [button]);
    try {
      const response = await fetch(`/api/repair-examples/export?${filters()}&include_source=${full ? 1 : 0}`);
      if (!response.ok) throw new Error('Could not export repair examples.');
      const url = URL.createObjectURL(await response.blob()), link = document.createElement('a');
      link.href = url; link.download = full ? 'repair-examples-full.json' : 'repair-examples-summary.json';
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { toast(esc(error.message), 'err'); }
    finally { done(); }
  }
  category.onchange = status.onchange = all.onchange = comparable.onchange = () => load();
  get('[data-refresh]').onclick = () => { investigation = null; return load(); }; get('[data-more]').onclick = () => load(false);
  get('[data-prompt]').onclick = async () => {
    try { await copyText(investigationConfig().then((data) => data.prompt)); toast('Analysis prompt copied. Attach the exported examples in your coding agent.'); }
    catch (error) { toast(esc(error.message), 'err'); }
  };
  get('[data-export]').onclick = (event) => download(false, event.currentTarget);
  get('[data-export-full]').onclick = (event) => download(true, event.currentTarget);
  get('[data-close]').onclick = () => dialog.close(); dialog.addEventListener('close', () => dialog.remove(), {once: true});
  dialog.showModal(); await load();
};
