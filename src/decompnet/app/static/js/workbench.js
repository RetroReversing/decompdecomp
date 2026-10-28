/* Queue evidence, budgets and comparisons. All paid trials require an explicit start click. */
async function showQueuePreview(id) {
  const panel=dialog('Agent queue preview','<div data-queue-results></div>');
  const box=panel.querySelector('[data-queue-results]'),done=beginWork(box,'Reading the scheduler');
  try {
    const data=await api(`/api/agents/queue?id=${encodeURIComponent(id)}`);
    if (!panel.isConnected) return;
    const rows=data.next.map((f,i)=>`<li><button class="link-btn" data-queue-function="${i}">${esc(f.name)}</button> · ${f.instructions} instructions${f.sdk?' · SDK library':''}</li>`).join('');
    box.innerHTML=`<p>${esc(data.note)}</p>${data.current?`<p>Current function: <b>${esc(data.current)}</b>. It stays with this worker.</p>`:''}<h3>Next functions · ${esc(data.strategy)}</h3><ol>${rows || '<li>No eligible functions.</li>'}</ol><h3>Why functions are excluded</h3><ul>${Object.entries(data.excluded_counts).map(([why,n])=>`<li>${esc(why)}: <b>${n}</b></li>`).join('') || '<li>No exclusions.</li>'}</ul><details><summary>Examples (up to 100)</summary><ul>${data.excluded.map(f=>`<li>${esc(f.name)} — ${esc(f.reasons.join('; '))}</li>`).join('')}</ul></details>`;
    box.querySelectorAll('[data-queue-function]').forEach(b=>{b.onclick=()=>{closeDialog();openFn(data.next[Number(b.dataset.queueFunction)].name);};});
  } catch(e){showJobError(box,e.message);} finally{done();}
}

function showAgentControls(s) {
  const fork=S.ov?.kind==='fork';
  const panel=dialog(`Limits · ${s.label}`,`${limitFields(s.limits)}<p class="small muted">Limits count from this start, or from saving these controls. Metric resets do not reset limits. The app checks about every two seconds. Provider token/dollar usage may arrive late; in-flight calls can overshoot, and a missing dollar report cannot enforce a dollar-only cap. Use an elapsed-time cap too.</p><label class="check"><input data-compiler-first type="checkbox" ${s.compiler_first?'checked':''} ${fork?'disabled':''}><span>Before each session, compare installed compilers for its next function when saved C is available</span></label><p class="small muted">Local checks use no AI tokens and leave compiler settings unchanged. Cached, current results are skipped. ${fork?'Imported-project installed-compiler comparisons are not supported yet; use the project’s compiler tools.':''}</p><button class="primary" data-controls-save>Save controls</button><button class="ghost" data-controls-cancel>Cancel</button>`);
  panel.querySelector('[data-controls-cancel]').onclick=closeDialog;
  panel.querySelector('[data-controls-save]').onclick=async()=>{
    try{await api('/api/agents/controls',{method:'POST',body:{id:s.id,limits:readLimits(panel),compiler_first:panel.querySelector('[data-compiler-first]').checked}});closeDialog();await poll(false);}
    catch(e){toast(esc(e.message),'err');}
  };
}

async function functionEvidence(kind) {
  const slug=S.slug,name=S.fn;
  const panel=dialog(kind==='models'?'Models on this function':'Observed differences','<div data-evidence></div>');
  const box=panel.querySelector('[data-evidence]'),done=beginWork(box,kind==='models'?'Reading model history':'Checking the best saved candidate');
  try{
    const data=await api(`/api/function/${kind}?slug=${encodeURIComponent(slug)}&name=${encodeURIComponent(name)}`);
    if(!panel.isConnected)return;
    if(kind==='models'){
      box.innerHTML=`<p class="small muted">${esc(data.note)}</p><div class="table-wrap"><table><thead><tr><th>Model</th><th>Checks</th><th>Improvements</th><th>Best</th><th>Project tokens</th><th>Project reported cost</th></tr></thead><tbody>${data.models.map(r=>`<tr><td>${esc(r.model)}<br><small>${esc(r.kind)}</small></td><td>${r.checks}</td><td>${r.improvements}</td><td>${r.best_percent==null?'—':`${r.best_percent.toFixed(1)}%`}${r.verified?' · verified':''}</td><td>${r.project_tokens==null?'Unknown':Number(r.project_tokens).toLocaleString()}</td><td>${r.project_usd==null?'Unknown':money(r.project_usd)}</td></tr>`).join('') || '<tr><td colspan="6">No recorded checks yet.</td></tr>'}</tbody></table></div>`;
    }else{
      box.innerHTML=`<p>${esc(data.note)}</p>${data.error?`<pre>${esc(data.error)}</pre>`:''}${data.groups.map((r,i)=>`<section class="panel pad"><h3>${esc(r.title)} · ${r.count}</h3><p>${esc(r.hint)}</p><button class="link-btn" data-diff-evidence="${i}">Show supporting instructions in best diff</button></section>`).join('') || '<p>No differing instructions available. Make a compiled candidate first, or inspect compiler/link errors.</p>'}`;
      box.querySelectorAll('[data-diff-evidence]').forEach(b=>{b.onclick=async()=>{
        if(S.slug!==slug||S.fn!==name)return;
        closeDialog();S.which='best';S.diffKey=null;showFnTab('diff',false);
        if(S.slug!==slug||S.fn!==name)return;
        S.diffKey=null;await loadDiff();
        document.getElementById(`diff-row-${data.groups[Number(b.dataset.diffEvidence)].rows[0]}`)?.scrollIntoView({block:'center'});
      };});
    }
  }catch(e){showJobError(box,e.message);}finally{done();}
}
$('fnModelEvidence').onclick=()=>functionEvidence('models');
$('fnDiagnosis').onclick=()=>functionEvidence('diagnosis');

function comparisonTable(report={}) {
  return `<p class="small muted">State: ${esc(report.state || 'not run')}${report.started_at?` · ${new Date(report.started_at*1000).toLocaleString()}`:''}. Budget: ${report.budget?.seconds || '—'}s${report.budget?.tokens?` / ${Number(report.budget.tokens).toLocaleString()} reported tokens`:''} per model. Sequential isolated trials; no results are merged into your project. Usage can be incomplete for interrupted calls. API-equivalent estimates are not subscription charges.</p>${report.active_trial?`<p>Trial ${report.active_trial.index}/${report.active_trial.total}: <b>${esc(report.active_trial.model)}</b></p>`:''}<div class="table-wrap"><table><thead><tr><th>Model</th><th>Time</th><th>Matches</th><th>Improved</th><th>Verified bytes</th><th>Bytes / million tokens</th><th>Reported tokens</th><th>Reported cost / estimate</th></tr></thead><tbody>${(report.trials||[]).map(t=>`<tr><td>${esc(t.model)}<br><small>${esc(t.state)} · ${esc(t.stop_reason || t.error || '')}${t.changed_targets?.length?` · ${t.changed_targets.length} changed targets excluded`:''}${t.regressions?.length?` · ${t.regressions.length} regressions`:''}</small></td><td>${Math.round(t.seconds||0)}s</td><td>${t.matches||0}${t.unique_matches?` (${t.unique_matches.length} exclusive)`:''}</td><td>${t.improved||0}</td><td>${t.verified_bytes||0}</td><td>${t.bytes_per_million_tokens==null?'—':Math.round(t.bytes_per_million_tokens)}</td><td>${t.usage?.tokens?Number(t.usage.tokens).toLocaleString():'Unknown'}</td><td>${t.usage?.usd?money(t.usage.usd):'Unknown'} / ${t.api_equivalent_estimate==null?'Unknown':`≈ ${money(t.api_equivalent_estimate)}`}</td></tr>`).join('')}</tbody></table></div>${report.error?`<p class="err-msg">${esc(report.error)}</p>`:''}${(report.trials||[]).map(t=>`<details><summary>${esc(t.model)} · matched functions and local files</summary><p>Trial folder: <code>${esc(t.folder || 'Unknown')}</code></p><p>Exclusive matches: ${esc((t.unique_matches || []).join(', ') || 'None')}</p><p>New matches: ${esc((t.matched_functions || []).slice(0,100).join(', ') || 'None')}${t.matched_functions?.length>100?' · first 100; full list in local report':''}</p></details>`).join('')}`;
}

function comparisonHistory(item) {
  if(!item)return comparisonTable();
  const older=(item.reports || []).filter(r=>r.id!==item.report?.id);
  return comparisonTable(item.report)+older.map(r=>`<details><summary>Earlier comparison · ${new Date(r.started_at*1000).toLocaleString()}</summary>${comparisonTable(r)}</details>`).join('');
}

async function showComparisons() {
  const slug=S.slug;
  const panel=dialog('Compare models from the same checkpoint',`<p>Stop this project’s agents before creating a checkpoint, and stop the app’s other agents before starting a controlled comparison. Each model receives its own copied files and database with the same starting state and limits. Automatic background matching is disabled in trials. Other work on your computer can affect timings. Trials continue if you close this panel; use Stop comparison to end them.</p><button class="ghost" data-checkpoint>Create checkpoint</button><label class="field"><span>Checkpoint</span><select data-checkpoint-id></select></label><div data-trials></div><button class="ghost" data-add-trial>Add model</button><h3>Equal budget for every trial</h3><label class="field"><span>Shared work order (adaptive routing is disabled)</span><select data-trial-strategy>${QUEUE_STRATEGIES.filter(([id])=>id && id!=="random").map(([id,label])=>`<option value="${id}" ${id==="easiest"?"selected":""}>${esc(label)}</option>`).join("")}</select></label><label class="field"><span>Seconds (finite ceiling, including token-only comparisons)</span><input data-trial-seconds type="number" min="1" max="604800" value="1800"></label><label class="field"><span>Reported tokens (optional)</span><input data-trial-tokens type="number" min="1"></label><label class="check"><input data-trial-builds type="checkbox"><span>Allow imported-project trials to execute their copied build scripts with my account permissions (not an OS sandbox)</span></label><label class="check"><input data-trial-confirm type="checkbox"><span>Starting trials uses my configured accounts and paid allowance.</span></label><div class="row"><button class="primary" data-run-trials>Start comparison</button><button class="ghost" data-stop-trials>Stop comparison</button><button class="ghost" data-refresh-trials>Refresh results</button></div><pre class="joblog" data-comparison-log></pre><div data-comparison-results></div>`);
  const ownResults=panel.querySelector('[data-comparison-results]');
  const select=panel.querySelector('[data-checkpoint-id]'),trials=panel.querySelector('[data-trials]'),log=panel.querySelector('[data-comparison-log]');
  let items=[],trialSequence=0;
  const add=()=>{
    if(trials.children.length>=6)return;
    const row=document.createElement('section');row.className='panel pad';
    const listId=`comparison-model-${++trialSequence}`;
    row.innerHTML=`<label class="field"><span>Agent</span><select data-trial-kind>${(S.st.agents||[]).filter(a=>a.installed||a.uses_endpoints).map(a=>`<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('')}</select></label><label class="field"><span>Exact model ID (include #effort when supported)</span><input data-trial-model list="${listId}" placeholder="Model ID"><datalist id="${listId}"></datalist></label><label class="field"><span>API endpoint (API agents only)</span><select data-trial-endpoint><option value="">Not using an API endpoint</option>${(S.st.settings.endpoints||[]).map(e=>`<option value="${esc(e.id)}">${esc(e.name||e.model||e.id)}</option>`).join('')}</select></label><button class="link-btn" data-remove-trial>Remove model</button>`;
    const updateKind=()=>{
      const chosen=(S.st.agents||[]).find(a=>a.id===row.querySelector('[data-trial-kind]').value);
      row.querySelector('[data-trial-endpoint]').closest('label').hidden=!chosen?.uses_endpoints;
      row.querySelector('datalist').innerHTML=(chosen?.models||[]).map(m=>`<option value="${esc(m.id)}">${esc(m.name || m.id)}</option>`).join('');
    };
    row.querySelector('[data-trial-kind]').onchange=updateKind;
    updateKind();
    row.querySelector('[data-remove-trial]').onclick=()=>row.remove();trials.append(row);
  };
  const refresh=async()=>{
    items=await api(`/api/comparisons?slug=${encodeURIComponent(slug)}`);
    if(!ownResults.isConnected)return;
    const previous=select.value;
    select.innerHTML=items.map(i=>`<option value="${i.id}">${new Date(i.created_at*1000).toLocaleString()} · ${esc(i.digest.slice(0,12))}</option>`).join('');
    if(items.some(i=>i.id===previous))select.value=previous;
    panel.querySelector('[data-comparison-results]').innerHTML=comparisonHistory(items.find(i=>i.id===select.value));
  };
  panel.querySelector('[data-add-trial]').onclick=add;add();add();
  select.onchange=()=>{panel.querySelector('[data-comparison-results]').innerHTML=comparisonHistory(items.find(i=>i.id===select.value));};
  const work=async(action)=>{
    const button=panel.querySelector(action==='checkpoint'?'[data-checkpoint]':'[data-run-trials]');
    const controls=[button,select,panel.querySelector('[data-checkpoint]'),panel.querySelector('[data-add-trial]'),...trials.querySelectorAll('input,select,button')];
    const done=beginWork(log,action==='checkpoint'?'Copying checkpoint':'Running isolated model trials',controls);
    try{
      const body=action==='checkpoint'?{action,slug}:{action:'run',id:select.value,confirmed:panel.querySelector('[data-trial-confirm]').checked,allow_builds:panel.querySelector('[data-trial-builds]').checked,selection:{strategy:panel.querySelector('[data-trial-strategy]').value},budget:{seconds:Number(panel.querySelector('[data-trial-seconds]').value),tokens:Number(panel.querySelector('[data-trial-tokens]').value)||0},plans:[...trials.children].map(row=>({kind:row.querySelector('[data-trial-kind]').value,model:row.querySelector('[data-trial-model]').value.trim(),endpoint:row.querySelector('[data-trial-endpoint]').value}))};
      const {job}=await api('/api/comparisons',{method:'POST',body});
      await followJob(job,log);await refresh();
    }catch(e){showJobError(log,e.message);}finally{done();}
  };
  panel.querySelector('[data-checkpoint]').onclick=()=>work('checkpoint');
  panel.querySelector('[data-run-trials]').onclick=()=>work('run');
  panel.querySelector('[data-refresh-trials]').onclick=()=>refresh().catch(e=>toast(esc(e.message),'err'));
  panel.querySelector('[data-stop-trials]').onclick=async()=>{try{await api('/api/comparisons',{method:'POST',body:{action:'stop',id:select.value}});}catch(e){toast(esc(e.message),'err');}};
  try{await refresh();}catch(e){showJobError(log,e.message);}
}
$('compareModels').onclick=showComparisons;
