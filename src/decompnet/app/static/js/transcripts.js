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
  let session = 0, after = 0, oldest = 0;
  const endpoint = () => `/api/transcript?slug=${encodeURIComponent(slug)}&helper=${encodeURIComponent(helper)}`;
  function showEvents(result, reset) {
    if (reset) { events.replaceChildren(); after = 0; }
    for (const entry of result.events) {
      const details = document.createElement("details"), summary = document.createElement("summary");
      const labels = {prompt: "Initial prompt", instructions: "App instructions", tool_definitions: "Tool definitions",
        tool_request: "Tool request", tool_response: "Tool response", api_request: "API request (actual context)",
        text: "Agent reply", think: "Exposed reasoning", usage: "Reported usage"};
      details.className = `transcript-event${entry.kind === "error" ? " transcript-event-error" : ""}`;
      details.open = entry.kind === "error" || (entry.kind === "text" && entry.text.length <= 2000);
      const label = document.createElement("span"), time = document.createElement("time"), size = document.createElement("span");
      label.className = "transcript-event-label"; label.textContent = labels[entry.kind] || entry.kind;
      time.textContent = clock(entry.created_at);
      size.className = "transcript-event-size"; size.textContent = `${entry.text.length.toLocaleString()} characters`;
      summary.append(label, time, size);
      const pre = document.createElement("pre"); pre.textContent = entry.text;
      const copy = document.createElement("button"); copy.type = "button"; copy.className = "ghost small-btn transcript-copy"; copy.textContent = "Copy text";
      copy.onclick = async () => { copy.textContent = await copyText(entry.text) ? "Copied" : "Copy failed — try again"; };
      details.append(summary, pre, copy); events.append(details); after = entry.id;
    }
    dialog.querySelector("[data-event-count]").textContent = `${events.children.length} events loaded`;
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
