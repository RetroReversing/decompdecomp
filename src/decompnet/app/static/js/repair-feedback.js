/* Captured match evidence and proposed rules stay local until the user downloads them. */
$("repairExamples").onclick = async () => {
  const slug = S.slug, dialog = document.createElement("dialog");
  dialog.className = "transcript-dialog";
  dialog.innerHTML = `<h2>Repair examples</h2>
    <p class="muted small">Successful matches captured automatically from this update onward. Agent explanations are hypotheses for review.
    The comparison uses the closest saved failed attempt; other code, header or compiler changes may also matter.
    No suggested rule is enabled automatically.</p>
    <div class="transcript-controls"><label><input type="checkbox" data-all> Across all projects</label>
    <label><input type="checkbox" data-comparable checked> Only with a failed candidate</label>
    <select data-category aria-label="Repair category"><option value="">All categories</option></select>
    <select data-status aria-label="Review state"><option value="">All review states</option></select>
    <button data-refresh type="button">Refresh</button><button data-close type="button">Close</button></div>
    <p data-count></p><div data-list></div><button data-more hidden type="button">Older examples</button>
    <p class="muted small">Full examples include source and available headers. Summaries omit these and the code diff; review notes may still quote code.
    Downloads stay on your computer.</p><button data-export type="button">Export summaries</button>
    <button data-export-full type="button">Export full examples</button>`;
  document.body.append(dialog);
  const get = (selector) => dialog.querySelector(selector);
  const list = get('[data-list]'), category = get('[data-category]'), status = get('[data-status]'), all = get('[data-all]');
  const comparable = get('[data-comparable]');
  comparable.checked = true;
  let before = 0;
  const scope = () => `slug=${encodeURIComponent(slug)}&all=${all.checked ? 1 : 0}`;
  const filters = () => `${scope()}&comparable=${comparable.checked ? 1 : 0}&category=${encodeURIComponent(category.value)}&status=${encodeURIComponent(status.value)}`;
  const controls = [category, status, all, comparable, get('[data-refresh]'), get('[data-more]')];
  function options(select, values) {
    if (select.options.length > 1) return;
    for (const value of values) { const option = document.createElement('option'); option.value = value; option.textContent = value.replaceAll('_', ' '); select.append(option); }
  }
  function card(row, states, categories) {
    const details = document.createElement('details'), summary = document.createElement('summary');
    summary.textContent = `#${row.id} · ${row.project} · ${row.function} · ${row.category.replaceAll('_', ' ')} · ${row.status}`;
    const body = document.createElement('div'); details.append(summary, body);
    details.addEventListener('toggle', async () => {
      if (!details.open || details.loaded) return;
      const finish = beginWork(body, 'Loading repair evidence');
      try {
        const data = await api(`/api/repair-examples?${scope()}&id=${row.id}`);
        const explanation = document.createElement('p');
        explanation.textContent = `${data.explanation || 'No agent explanation supplied.'}${data.automation ? ' Suggested automation: ' + data.automation : ''}`;
        const pre = document.createElement('pre'); pre.textContent = JSON.stringify(data.evidence, null, 2);
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
        body.append(explanation, review, kind, note, save, pre); details.loaded = true;
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
      if (reset) list.replaceChildren();
      options(category, data.categories); options(status, data.states);
      get('[data-count]').textContent = data.groups.map((group) => `${group.count} ${group.category.replaceAll('_', ' ')} (${group.status})`).join(' · ') || 'No examples captured yet.';
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
  get('[data-refresh]').onclick = () => load(); get('[data-more]').onclick = () => load(false);
  get('[data-export]').onclick = (event) => download(false, event.currentTarget);
  get('[data-export-full]').onclick = (event) => download(true, event.currentTarget);
  get('[data-close]').onclick = () => dialog.close(); dialog.addEventListener('close', () => dialog.remove(), {once: true});
  dialog.showModal(); await load();
};
