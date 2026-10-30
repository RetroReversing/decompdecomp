// Exercise real card rendering without any agent calls or live project data.
const fs=require('fs'), vm=require('vm'), assert=require('assert'), path=require('path');
const elements=new Map();
const el=()=>({hidden:false,querySelectorAll:()=>[]});
const c={Set,Map,Number,Date,Math,String,document:{addEventListener:()=>{}},
  $:id=>{if(!elements.has(id))elements.set(id,el());return elements.get(id);},
  agentSort:'state',S:{claims:[],stats:{agents:[]}},LIVE:['working'],STATE_ORDER:{working:0,stopped:1},
  esc:s=>String(s??''),money:n=>'$'+n.toFixed(2),plural:(n,s)=>`${n} ${s}s`,
  rowTint:()=>'',helperState:()=> 'Working on function',helperResults:()=>'',
  hhmm:()=> '12:00',renderCosts:()=>{},renderRoomAgents:()=>{},
  setHtml:(e,h)=>{e.html=h;},sentence:s=>s};
vm.createContext(c);
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/agents.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('function agentRuntime('),source.indexOf('async function showAgentResults(')),c);
vm.runInContext(source.slice(source.indexOf('const agentDetailsOpen'),source.indexOf('$("helperLogClose").onclick')),c);
vm.runInContext(source.slice(source.indexOf('const QUEUE_STRATEGIES'),source.indexOf('function selectionFields(')),c);
const agent={id:'test',agent:'test',label:'Example agent',kind_name:'CLI',model:'example#low',state:'working',
  tokens:51480188,estimated_cost:9.46,started_at:Date.now()/1000-2880,per_session:8,
  metrics:{matched:1,improved:13,compiles:1506,last_improvement:Date.now()/1000-1920},last_message:'Testing another candidate'};
c.renderSessions([agent]);
let html=elements.get('sessions').html;
assert(html.indexOf('data-result-kind="matched"')<html.indexOf('class="state'));
assert(html.indexOf('data-result-kind="improved"')<html.indexOf('class="state'));
const menu=html.slice(html.indexOf('class="agent-menu"'),html.indexOf('</details>'));
for(const action of ['data-rename','data-queue','data-log','data-reset-metrics'])assert(menu.includes(action));
assert(menu.includes('What it said and did'));
assert(html.includes('51.5M tokens'));assert(html.includes('API-equivalent'));
assert(html.includes('51,480,188 total tokens used'));assert(html.includes('8 functions/session'));
assert(!html.match(/data-agent-details="test" open/));
vm.runInContext("agentDetailsOpen.add('test')",c);agent.metrics.improved++;
c.renderSessions([agent]);html=elements.get('sessions').html;
assert(html.includes('data-agent-details="test" open'));assert(html.includes('<strong>14</strong>'));
agent.state='stopped';c.renderSessions([agent]);html=elements.get('sessions').html;
assert(html.includes('Start again'));assert(html.includes('Remove agent'));assert(!html.includes('data-stop='));
assert.equal(c.compactAgentNumber(999), '999');assert.equal(c.compactAgentNumber(5000), '5k');
