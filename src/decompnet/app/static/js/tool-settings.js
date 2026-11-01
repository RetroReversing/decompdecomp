/* Optional tool policy uses the same catalog as the historical transcript and MCP setup. */
async function loadToolSettings(host, log, helper = '') {
  const done = beginWork(log, 'Loading agent tools');
  try {
    const info = await api(`/api/agent-tools${helper ? `?helper=${encodeURIComponent(helper)}` : ''}`);
    if (!host.isConnected) return;
    host.replaceChildren(toolCatalog([...info.tools].sort((a,b)=>a.calls-b.calls || a.name.localeCompare(b.name)), {editable:true, helper, onToggle:async(name, enabled, toggle)=>{
      log.textContent=''; log.hidden=true;
      const finish = beginWork(log, 'Saving tool settings', [toggle]);
      try {
        await api('/api/agent-tools',{method:'POST',body:{helper,name,enabled}});
        toast('Tool settings saved. Start a new session or reconnect MCP to refresh the tool list.','match');
      } catch(error) { toggle.checked=!enabled; showJobError(log,error.message); }
      finally { finish(); }
    }}));
    if (!helper) $('agentToolCoverage').textContent='Least used first. ' + info.coverage;
  } catch(error) { showJobError(log,error.message); }
  finally { done(); }
}
function loadGlobalAgentTools() { $('agentToolsRefresh').onclick=loadGlobalAgentTools; return loadToolSettings($('agentToolCatalog'),$('agentToolLog')); }
async function showAgentTools(session) {
  if (!session) return;
  const panel=dialog(`Tools · ${session.label}`, '<p class="small muted">Disable additional optional tools for this agent. Counts are across all agents on this computer. Globally disabled tools and core tools cannot be changed here. Tool lists refresh at the next session or MCP reconnect.</p><div data-tool-catalog></div><pre data-tool-log hidden></pre><button data-close-tools type="button">Close</button>');
  panel.querySelector('[data-close-tools]').onclick=closeDialog;
  await loadToolSettings(panel.querySelector('[data-tool-catalog]'),panel.querySelector('[data-tool-log]'),session.id);
}
