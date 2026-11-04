const $ = id => document.getElementById(id);
const esc = text => String(text ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const fmt = number => Number(number || 0).toLocaleString();
const percent = summary => summary.bytes ? Math.min(100, summary.matched_bytes / summary.bytes * 100) : 0;
const date = time => new Date(time * 1000).toLocaleString();
let page = 0, listRequest = 0, viewRequest = 0, registration = null, activeFunctions = [];
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'Content-Type': 'application/json', ...options.headers}});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Could not load the directory. Please try again.');
  return data;
}
function card(project) {
  return `<a class="card" href="#project=${project.id}"><div class="card-top"><span class="tag">${esc(project.platform)}</span><span class="status ${project.online ? 'online' : ''}">${project.online ? 'Host reached' : 'Host unavailable'}</span></div><h3>${esc(project.title)}</h3><div class="card-stats"><strong>${percent(project.summary).toFixed(1)}<small>%</small></strong><span>of code matched</span></div><div class="progress"><i style="width:${percent(project.summary)}%"></i></div><span class="muted">${fmt(project.summary.matched)} / ${fmt(project.summary.functions)} functions matched</span><div class="card-bottom"><span>View project ↗</span><span title="${esc(date(project.success_at))}">Saved ${esc(new Date(project.success_at*1000).toLocaleDateString())}</span></div></a>`;
}
async function loadList() {
  const request = ++listRequest;
  $('cards').innerHTML = '<div class="empty"><span class="spinner"></span> Loading projects…</div>';
  try {
    const data = await api(`/api/projects?page=${page}&q=${encodeURIComponent($('search').value)}`);
    if (request !== listRequest) return;
    $('count').textContent = data.projects.length ? `Page ${page+1}` : '';
    $('cards').innerHTML = data.projects.map(card).join('') || '<div class="empty"><h3>A place for your next project.</h3><p class="muted">No projects found. List a public room to share its progress here.</p></div>';
    $('previous').hidden = page === 0; $('next').hidden = !data.more;
  } catch (error) {
    if (request !== listRequest) return;
    $('cards').innerHTML = `<div class="empty error">${esc(error.message)} <button id="retryList">Try again</button></div>`;
    $('retryList').onclick=loadList;
  }
}
async function route() {
  const request = ++viewRequest;
  const id = new URLSearchParams(location.hash.slice(1)).get('project');
  ++listRequest;
  $('directory').hidden = !!id; $('project').hidden = !id;
  if (!id) { loadList(); return; }
  $('project').innerHTML = '<p class="notice"><span class="spinner"></span> Loading project snapshot…</p>';
  if (!/^[a-f0-9]{32}$/.test(id)) { $('project').textContent = 'Project not found.'; return; }
  try {
    const project = await api('/api/projects/'+id);
    if (request !== viewRequest) return;
    const data = project.snapshot;
    activeFunctions = data.functions;
    let ownerKey = '';
    try { ownerKey = localStorage.getItem('directory-owner:'+id) || ''; } catch {}
    $('project').innerHTML = `<a class="back" href="#">← All projects</a><div class="project-heading"><div><span class="tag">${esc(project.platform)}</span><h1>${esc(project.title)}</h1><p class="muted">${esc(data.compiler || 'Compiler not specified')}</p></div>${project.online ? `<a class="join primary" href="${esc(project.join)}" target="_blank" rel="noopener noreferrer">Open hosted room ↗</a>` : '<span class="status">Room unavailable</span>'}</div><div class="notice"><b>${project.online ? 'Host was reachable at the last check.' : 'Host unavailable. Showing its last saved snapshot.'}</b> Last fetched ${esc(date(project.success_at))}. Progress is reported by the host; the directory does not independently verify matches. Rooms require the owner’s app to be online.</div><div class="card-stats"><strong>${percent(project.summary).toFixed(1)}%</strong><span>${fmt(project.summary.matched)} / ${fmt(project.summary.functions)} functions matched</span></div><div class="progress"><i style="width:${percent(project.summary)}%"></i></div><section class="map-panel"><h2>Function map</h2><div class="legend"><span style="--swatch:#55554b">To do</span><span style="--swatch:#a6b879">Tried</span><span style="--swatch:#31bb70">Matched</span><span style="--swatch:#b88d66">Assembly / set aside</span></div><p class="hint">Each square is a function in project order. Select one to find it in the list.</p><canvas id="map" aria-label="Function progress map; the searchable table below provides the same function information" role="img"></canvas><div class="function-label" id="functionLabel">Point to a square to inspect its status.</div></section><div class="toolbar"><h2>Functions <span class="muted">${fmt(data.functions.length)}</span></h2><label class="search"><span class="sr">Find a function</span><input type="search" id="functionSearch" placeholder="Find a function…"></label></div><div class="table-wrap"><table><thead><tr><th>FUNCTION</th><th>STATE</th><th>SIZE</th><th>MISMATCH SCORE</th></tr></thead><tbody id="functions"></tbody></table></div><p class="hint" id="functionCount"></p><details><summary>Manage this listing</summary><p class="hint">Use the removal key saved when this project was published.</p><label class="field">Removal key<input type="password" id="removeKey" value="${esc(ownerKey)}" autocomplete="off"></label><button id="removeListing">Remove listing</button><p id="removeStatus" role="status"></p></details>`;
    drawMap(); renderFunctions();
    $('functionSearch').oninput = renderFunctions;
    $('removeListing').onclick = async () => {
      if (!confirm('Remove this project from the public directory? Its hosted room is unaffected.')) return;
      const button = $('removeListing'); button.disabled = true; button.innerHTML = '<span class="spinner"></span>Removing…';
      try {
        await api('/api/projects/'+id,{method:'DELETE',headers:{Authorization:'Bearer '+$('removeKey').value}});
        try { localStorage.removeItem('directory-owner:'+id); } catch {}
        location.hash = '';
      } catch (error) { $('removeStatus').textContent = error.message; }
      finally { button.disabled=false; button.textContent='Remove listing'; }
    };
  } catch (error) { if (request === viewRequest) $('project').innerHTML = `<a class="back" href="#">← All projects</a><p class="error">${esc(error.message)}</p>`; }
}
function renderFunctions() {
  const query = $('functionSearch').value.toLowerCase();
  const rows = activeFunctions.filter(row => row[0].toLowerCase().includes(query));
  $('functions').innerHTML = rows.slice(0,200).map(row => `<tr><td>${esc(row[0])}</td><td>${esc(row[2].replaceAll('_',' '))}</td><td>${fmt(row[1])} B</td><td>${row[3] === null ? '—' : fmt(row[3])}</td></tr>`).join('');
  $('functionCount').textContent = `Showing ${Math.min(200,rows.length)} of ${fmt(rows.length)} functions. ${rows.length > 200 ? 'Search to narrow the list. ' : ''}Lower mismatch scores are better; zero means an exact match reported by the host.`;
}
function drawMap() {
  const canvas = $('map'); if (!canvas || $('project').hidden) return;
  const width = canvas.getBoundingClientRect().width;
  const columns = Math.max(1,Math.floor(width/12));
  const cell = width/columns, height = Math.max(1,Math.ceil(activeFunctions.length/columns))*cell;
  const scale = devicePixelRatio || 1;
  canvas.width = Math.ceil(width*scale); canvas.height = Math.ceil(height*scale); canvas.style.height=height+'px';
  const ctx = canvas.getContext('2d'); ctx.scale(scale,scale);
  activeFunctions.forEach((row,index) => {
    ctx.fillStyle = row[2] === 'matched' ? '#31bb70' : ['asm','not_c','set_aside'].includes(row[2]) ? '#b88d66' : row[3] !== null ? '#a6b879' : '#55554b';
    ctx.fillRect(index%columns*cell,Math.floor(index/columns)*cell,cell-3,cell-3);
  });
  const find = event => activeFunctions[Math.floor(event.offsetY/cell)*columns + Math.floor(event.offsetX/cell)];
  canvas.onmousemove = event => { const row = find(event); if(row) $('functionLabel').textContent=`${row[0]} · ${row[2]} · ${fmt(row[1])} bytes`; };
  canvas.onclick = event => { const row=find(event); if(row){$('functionSearch').value=row[0];renderFunctions();$('functionSearch').scrollIntoView({behavior:'smooth',block:'center'});} };
}
window.addEventListener('resize',drawMap);
window.addEventListener('hashchange',route);
$('add').onclick = () => $('publish').showModal();
$('close').onclick = () => $('publish').close();
$('previous').onclick = () => {page=Math.max(0,page-1);loadList();};
$('next').onclick = () => {page++;loadList();};
let searchTimer;
$('search').oninput = () => {clearTimeout(searchTimer); searchTimer=setTimeout(()=>{page=0;loadList();},250);};
async function publishing(button, text, work) {
  const label=button.textContent; button.disabled=true;button.innerHTML='<span class="spinner"></span>'+esc(text);
  $('publishBar').hidden=false; $('publishStatus').className=''; $('publishStatus').textContent=text;
  try {await work();}
  catch(error){$('publishStatus').className='error';$('publishStatus').textContent=error.message;}
  finally{button.disabled=false;button.textContent=label;$('publishBar').hidden=true;}
}
$('publishForm').onsubmit = event => {
  event.preventDefault();
  publishing($('prepare'),'Preparing publishing code…',async()=>{
    registration=await api('/api/registrations',{method:'POST',body:JSON.stringify({url:$('host').value})});
    $('code').textContent=registration.challenge; $('proofStep').hidden=false;$('published').hidden=true;
    $('publishStatus').textContent='Code ready. Save it in the host app, then verify below.';
  });
};
$('copy').onclick=async()=>{try{await navigator.clipboard.writeText($('code').textContent);$('copy').textContent='Copied';}catch{$('publishStatus').textContent='Select and copy the publishing code.';}};
$('verify').onclick=()=>publishing($('verify'),'Fetching public metadata…',async()=>{
  const result=await api('/api/publish',{method:'POST',body:JSON.stringify({id:registration.id})});
  try{localStorage.setItem('directory-owner:'+result.id,result.ownerKey);}catch{}
  $('proofStep').hidden=true;$('published').hidden=false;
  $('published').innerHTML=`<h3>Your project is listed.</h3><p>Save this removal key somewhere private. It lets you remove your listing from another browser.</p><div class="code-row"><code>${esc(result.ownerKey)}</code></div><p><a href="#project=${result.id}" id="viewPublished">View your project →</a></p>`;
  $('viewPublished').onclick=()=>$('publish').close();$('publishStatus').textContent='Published successfully.';
});
route();
