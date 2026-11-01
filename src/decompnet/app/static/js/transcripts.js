/* Markdown is escaped first: transcript content can never introduce HTML or script URLs. */
function transcriptMarkdown(text) {
  const escape = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const inline = text => {
    const codes = [];
    let safe = escape(text).replace(/`([^`]+)`/g, (_, code) => { codes.push(`<code>${code}</code>`); return `\u0000${codes.length - 1}\u0000`; });
    safe = safe.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*]+)\*/g, '<em>$1</em>');
    return safe.replace(/\u0000(\d+)\u0000/g, (_, index) => codes[Number(index)] || "");
  };
  const lines = text.split("\n"), blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const fence = line.match(/^\s*(`{3,}|~{3,})([\w+-]*)/);
    if (fence) {
      const code = []; i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) code.push(lines[i++]);
      if (i < lines.length) i++;
      blocks.push(`<pre><code>${escape(code.join("\n"))}</code></pre>`); continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) { const level = Math.min(6, heading[1].length + 1); blocks.push(`<h${level}>${inline(heading[2])}</h${level}>`); i++; continue; }
    if (/^\s*(?:---+|\*\*\*+)\s*$/.test(line)) { blocks.push('<hr>'); i++; continue; }
    if (/^\s*>/.test(line)) {
      const quote = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*> ?/, ""));
      blocks.push(`<blockquote>${transcriptMarkdown(quote.join("\n"))}</blockquote>`); continue;
    }
    const list = line.match(/^\s*(?:([-+*])|(\d+)[.)])\s+(.+)$/);
    if (list) {
      const tag = list[2] ? 'ol' : 'ul', items = [];
      while (i < lines.length) {
        const item = lines[i].match(/^\s*(?:([-+*])|(\d+)[.)])\s+(.+)$/);
        if (!item || Boolean(item[2]) !== Boolean(list[2])) break;
        items.push(`<li>${inline(item[3])}</li>`); i++;
      }
      blocks.push(`<${tag}>${items.join("")}</${tag}>`); continue;
    }
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i + 1])) {
      const cells = row => row.trim().replace(/^\||\|$/g, '').split('|');
      const head = cells(line).map(c => `<th>${inline(c.trim())}</th>`).join(''); i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(`<tr>${cells(lines[i++]).map(c => `<td>${inline(c.trim())}</td>`).join('')}</tr>`);
      blocks.push(`<div class="transcript-md-table"><table><thead><tr>${head}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`); continue;
    }
    blocks.push(`<p>${inline(line)}</p>`); i++;
  }
  return blocks.join("\n");
}
function transcriptToolResponse(text) {
  let response;
  try { response = JSON.parse(text); } catch { return null; }
  if (!response || typeof response !== "object" || typeof response.name !== "string") return null;
  const content = response.content ?? response.error;
  if (typeof content !== "string") return null;
  const body = document.createElement("div"); body.className = "transcript-markdown";
  body.innerHTML = transcriptMarkdown(content);
  return {body, name:response.name};
}
/* Only explicit IDs (or an unambiguous legacy name) link concurrent tool exchanges. */
function transcriptToolPayload(text) {
  try {
    const value = JSON.parse(text), name = value?.name || value?.function?.name;
    return typeof name === 'string' && name.trim() ? {...value, name} : null;
  } catch { return null; }
}
function transcriptToolTitle(request) {
  let args = request.arguments ?? request.function?.arguments ?? {};
  if (typeof args === 'string') { try { args = JSON.parse(args); } catch { return request.name; } }
  const entries = args && typeof args === 'object' ? Object.entries(args) : [];
  const preview = entries.slice(0, 4).map(([key, value]) => {
    let text = Array.isArray(value) ? `[${value.length} items]` : value && typeof value === 'object' ? `{${Object.keys(value).length} fields}` : JSON.stringify(value);
    text = String(text).replace(/\s+/g, ' ');
    if (text.length > 55) text = text.slice(0, 54) + '…';
    return `${key}=${text}`;
  }).join(', ') + (entries.length > 4 ? ', …' : '');
  return `${request.name}(${preview.length > 160 ? preview.slice(0, 159) + '…' : preview})`;
}
/* Readable tool catalog; unknown payloads always retain their original text view. */
function transcriptToolCatalog(text) {
  let tools;
  try { tools = JSON.parse(text); } catch { return null; }
  if (!Array.isArray(tools) || !tools.length || !tools.every(t => t && typeof t === "object" && typeof (t.function || t).name === "string")) return null;
  const catalog = document.createElement("div"); catalog.className = "transcript-tools";
  const heading = document.createElement("p"); heading.className = "small muted";
  heading.textContent = `${tools.length} tools available to this agent`; catalog.append(heading);
  for (const item of tools) {
    const tool = item.function || item, schema = tool.input_schema || tool.parameters || {};
    const card = document.createElement("details"); card.className = "transcript-tool";
    const summary = document.createElement("summary"), name = document.createElement("code"), description = document.createElement("p");
    name.textContent = tool.name; summary.append(name); description.textContent = tool.description || "No description provided.";
    card.append(summary, description);
    const properties = schema.properties;
    if (properties && typeof properties === "object" && Object.keys(properties).length) {
      const list = document.createElement("dl"); list.className = "transcript-tool-params";
      for (const [key, value] of Object.entries(properties)) {
        const param = value && typeof value === "object" ? value : {};
        const term = document.createElement("dt"), detail = document.createElement("dd");
        term.textContent = `${key}${Array.isArray(schema.required) && schema.required.includes(key) ? " · required" : " · optional"}`;
        const type = Array.isArray(param.type) ? param.type.join(" / ") : param.type || (param.enum ? "enum" : "value");
        detail.textContent = [type, param.description, param.enum ? `Choices: ${JSON.stringify(param.enum)}` : "", Object.hasOwn(param, "default") ? `Default: ${JSON.stringify(param.default)}` : ""].filter(Boolean).join(" · ");
        list.append(term, detail);
      }
      card.append(list);
    } else {
      const empty = document.createElement("p"); empty.className = "small muted";
      empty.textContent = "No named parameters."; card.append(empty);
    }
    catalog.append(card);
  }
  return catalog;
}
/* Fetch transcripts only on request: full prompts and C must never ride along in the live poll. */
$("helperTranscript").onclick = async () => {
  const helper = S.logFor, slug = S.slug;
  if (!helper) return;
  const dialog = document.createElement("dialog");
  dialog.className = "transcript-dialog session-transcript";
  dialog.innerHTML = `<header class="transcript-header"><div><span class="transcript-eyebrow">Agent activity</span><h2>Session transcript</h2><p class="small muted">Prompts, replies and tool exchanges from this helper.</p></div><button data-close type="button" class="ghost" aria-label="Close session transcript">Close</button></header>
    <div class="transcript-toolbar"><label>Session<select aria-label="Session"></select></label><div class="row">
    <button data-older type="button" class="ghost small-btn">Older sessions</button><button data-refresh type="button" class="ghost small-btn">Refresh</button></div></div>
    <div class="transcript-usage"><span class="transcript-eyebrow">Reported usage</span><p data-usage></p></div>
    <div class="transcript-timeline"><div class="transcript-timeline-heading"><h3>Timeline</h3><span data-event-count class="small muted"></span></div><div data-events></div><button data-more type="button" class="ghost" hidden>Load more messages</button></div>
    <details class="transcript-coverage"><summary>What this transcript includes</summary><p class="small muted">App prompts, exposed replies and full app tool exchanges recorded while transcripts were enabled. CLI internal system prompts, hidden reasoning, shell output and context compaction are not captured. API requests include the context actually sent. Credentials are redacted. Earlier sessions cannot be reconstructed.</p></details>`;
  document.body.append(dialog);
  const select = dialog.querySelector("select"), events = dialog.querySelector("[data-events]");
  const older = dialog.querySelector("[data-older]"), more = dialog.querySelector("[data-more]");
  const refresh = dialog.querySelector("[data-refresh]");
  let session = 0, after = 0, oldest = 0, eventCount = 0;
  const pendingTools = new Map();
  const endpoint = () => `/api/transcript?slug=${encodeURIComponent(slug)}&helper=${encodeURIComponent(helper)}`;
  function showEvents(result, reset) {
    if (reset) { events.replaceChildren(); after = 0; eventCount = 0; pendingTools.clear(); }
    for (const entry of result.events) {
      const details = document.createElement("details"), summary = document.createElement("summary");
      const labels = {prompt: "Initial prompt", instructions: "App instructions", tool_definitions: "Tool definitions",
        tool_request: "Tool request", tool_response: "Tool response", api_request: "API request (actual context)",
        handoff: "Session handoff", text: "Agent reply", think: "Exposed reasoning", usage: "Reported usage"};
      details.className = `transcript-event${entry.kind === "error" ? " transcript-event-error" : ""}`;
      details.open = entry.kind === "error" || (entry.kind === "text" && entry.text.length <= 2000);
      const label = document.createElement("span"), time = document.createElement("time"), size = document.createElement("span");
      label.className = "transcript-event-label"; label.textContent = labels[entry.kind] || entry.kind;
      const tool = ['tool_request', 'tool_response'].includes(entry.kind) ? transcriptToolPayload(entry.text) : null;
      if (entry.kind === 'tool_request' && tool) label.textContent = `Tool · ${transcriptToolTitle(tool)}`;
      time.textContent = clock(entry.created_at);
      size.className = "transcript-event-size"; size.textContent = `${entry.text.length.toLocaleString()} characters`;
      summary.append(label, time, size);
      const pre = document.createElement("pre"); pre.textContent = entry.text;
      const copy = document.createElement("button"); copy.type = "button"; copy.className = "ghost small-btn transcript-copy"; copy.textContent = "Copy text";
      copy.onclick = async () => { copy.textContent = await copyText(entry.text) ? "Copied" : "Copy failed — try again"; };
      let response = entry.kind === "tool_response" ? transcriptToolResponse(entry.text) : null;
      if (entry.kind === "handoff") {
        try {
          const handoff = JSON.parse(entry.text);
          const lines = ['Historical continuation facts. Check current candidates and claims.'];
          for (const fn of handoff.functions || []) {
            lines.push(`### ${fn.name}`, `Best historical score: ${fn.best_score ?? 'unscored'}`, fn.history || '', ...(fn.notes || []));
          }
          if (handoff.last_reply) lines.push('### Last recorded reply', handoff.last_reply);
          response = transcriptToolResponse(JSON.stringify({name: 'session_handoff', content: lines.join('\n\n')}));
        } catch { /* Retain raw legacy or incomplete handoff data. */ }
      }
      const catalog = entry.kind === "tool_definitions" ? transcriptToolCatalog(entry.text) : null;
      if (response) {
        const raw = document.createElement("details"), rawLabel = document.createElement("summary");
        raw.className = "transcript-tool-raw"; rawLabel.textContent = "Raw response (JSON)";
        raw.append(rawLabel, pre); details.append(summary, response.body, raw, copy);
        label.textContent = entry.kind === "handoff" ? "Session handoff" : `Tool response · ${response.name}`;
      } else if (catalog) {
        const raw = document.createElement("details"), rawLabel = document.createElement("summary");
        raw.className = "transcript-tool-raw"; rawLabel.textContent = "Full tool definitions (JSON)";
        raw.append(rawLabel, pre); details.append(summary, catalog, raw, copy);
        label.textContent = `Available tools · ${catalog.children.length - 1}`;
      } else details.append(summary, pre, copy);
      let parent = null;
      if (tool) {
        const key = tool.call_id ? `id:${tool.call_id}` : `name:${tool.name}`;
        if (entry.kind === 'tool_request') {
          details.className += ' transcript-tool-exchange';
          // Keep raw request JSON behind its own disclosure inside the shared call card.
          const args = document.createElement('details'), argsLabel = document.createElement('summary');
          args.className = 'transcript-tool-arguments'; argsLabel.textContent = 'Request arguments';
          args.append(argsLabel, pre, copy); details.replaceChildren(); details.append(summary, args);
          size.textContent = `${entry.text.length.toLocaleString()} request characters · awaiting response`;
          const waiting = pendingTools.get(key) || [];
          waiting.push({details, size, name:tool.name, length:entry.text.length}); pendingTools.set(key, waiting);
        } else {
          const waiting = pendingTools.get(key) || [];
          if (waiting.length === 1 && waiting[0].name === tool.name) {
            parent = waiting[0]; pendingTools.delete(key);
            label.textContent = tool.error ? 'Response · error' : 'Response';
            details.className += ' transcript-tool-result';
            details.open = true;
            parent.details.append(details);
            if (tool.error) { parent.details.className += ' transcript-event-error'; details.open = true; }
            parent.size.textContent = `${parent.length.toLocaleString()} request · ${entry.text.length.toLocaleString()} response characters`;
          }
        }
      }
      if (!parent) events.append(details);
      after = entry.id; eventCount++;
    }
    dialog.querySelector("[data-event-count]").textContent = `${eventCount} events loaded`;
    more.hidden = !result.more;
    if (!events.children.length) events.textContent = "No transcript was recorded for this session. Earlier sessions cannot be reconstructed.";
    const row = result.session;
    dialog.querySelector("[data-usage]").textContent = row && (row.input_tokens || row.output_tokens || row.cache_read_tokens || row.cache_write_tokens)
      ? `Reported tokens: ${row.input_tokens.toLocaleString()} input (excluding cache), ${row.cache_read_tokens.toLocaleString()} cache read, ${row.cache_write_tokens.toLocaleString()} cache write, ${row.output_tokens.toLocaleString()} output. ${row.usd ? money(row.usd) + " reported cost." : "No dollar cost reported."}`
      : row?.tokens ? `${row.tokens.toLocaleString()} tokens reported; input/cache/output breakdown unavailable.`
      : "No token usage reported yet; this does not mean the session was free.";
  }
  async function load(reset = true, old = false) {
    const finish = beginWork(events, "Loading transcript", [select, older, more, refresh]);
    try {
      const result = await api(endpoint() + `&session=${old ? session : reset ? Number(select.value || 0) : session}&after=${reset ? 0 : after}&before=${old ? oldest : 0}`);
      if (!dialog.isConnected) return;
      for (const row of result.sessions) {
        if ([...select.options].some((option) => option.value === String(row.id))) continue;
        const option = document.createElement("option"); option.value = row.id;
        option.textContent = `${new Date(row.started_at * 1000).toLocaleString()} · ${row.model || row.kind} · ${row.outcome || "running / interrupted"}`;
        select.append(option);
      }
      if (result.sessions.length && (old || !oldest)) oldest = result.sessions.at(-1).id;
      if (!old) { session = result.session?.id || 0; select.value = String(session); showEvents(result, reset); }
      older.hidden = result.sessions.length < 30;
    } catch (error) { toast(esc(error.message), "err"); }
    finally { finish(); }
  }
  select.onchange = () => load(); refresh.onclick = () => load();
  more.onclick = () => load(false); older.onclick = () => load(false, true);
  dialog.querySelector("[data-close]").onclick = () => dialog.close();
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  dialog.showModal(); await load();
};
