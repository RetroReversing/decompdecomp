/* Fetch transcripts only on request: full prompts and C must never ride along in the live poll. */
$("helperTranscript").onclick = async () => {
  const helper = S.logFor, slug = S.slug;
  if (!helper) return;
  const dialog = document.createElement("dialog");
  dialog.className = "transcript-dialog";
  dialog.innerHTML = `<h2>Session transcript</h2>
    <p class="muted small">App prompts, exposed replies and full app tool exchanges, recorded from this update onward.
    CLI internal system prompts, hidden reasoning, shell output and context compaction are not captured.
    API requests include the context actually sent. Credentials are redacted.</p>
    <div class="transcript-controls"><select aria-label="Session"></select>
    <button data-older type="button">Older sessions</button><button data-refresh type="button">Refresh</button>
    <button data-close type="button">Close</button></div>
    <p data-usage></p><div data-events></div><button data-more type="button" hidden>Load more messages</button>`;
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
      summary.textContent = `${clock(entry.created_at)} · ${labels[entry.kind] || entry.kind} · ${entry.text.length.toLocaleString()} characters`;
      const pre = document.createElement("pre"); pre.textContent = entry.text;
      details.append(summary, pre); events.append(details); after = entry.id;
    }
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
