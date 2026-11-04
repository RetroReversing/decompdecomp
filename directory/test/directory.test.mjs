import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import worker, {api, publicFetch} from '../src/worker.mjs';
import {roomAddress, publicIP, snapshot, readJSON} from '../src/protocol.mjs';

const demo = () => ({version:1,room:'demo',title:'Demo project',platform:'Demo console',compiler:'demo-cc',
  summary:{functions:2,matched:1,bytes:64,matched_bytes:32},
  functions:[['first',32,'matched',0,8],['second',32,'open',10,8]]});
function environment() {
  const db=new DatabaseSync(':memory:');
  db.exec(readFileSync(new URL('../migrations/0001_directory.sql',import.meta.url),'utf8'));
  function prepare(sql) {
    let values=[];
    return {bind(...args){values=args;return this;},async first(){return db.prepare(sql).get(...values)||null;},
      async all(){return {results:db.prepare(sql).all(...values)};},async run(){return db.prepare(sql).run(...values);}};
  }
  const objects=new Map();
  return {DB:{prepare,async batch(statements){return Promise.all(statements.map(statement=>statement.run()));}},
    SNAPSHOTS:{async put(key,value){objects.set(key,value);},async get(key){return objects.has(key)?{json:async()=>JSON.parse(objects.get(key))}:null;},async delete(key){objects.delete(key);}},db,objects};
}
const post=(path,data)=>new Request('https://directory.example'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
const dns = () => Response.json({Answer:[{type:1,data:'93.184.216.34'}]});

test('only HTTPS public room links without credentials or private invites are accepted',()=>{
  assert.deepEqual(roomAddress('https://room.example/join/open#room=demo'),{origin:'https://room.example',room:'demo'});
  for(const address of ['http://room.example','https://127.0.0.1','https://[::1]','https://localhost','https://host.internal','https://user:pass@room.example','https://room.example:6481','https://room.example/join/SECRET']) assert.throws(()=>roomAddress(address));
});
test('private and special-use addresses cannot be fetched',()=>{
  for(const ip of ['127.0.0.1','10.0.0.1','192.168.1.1','172.16.0.1','169.254.169.254','100.64.0.1','::1','fc00::1','::ffff:127.0.0.1','2001:db8::1']) assert.equal(publicIP(ip),false,ip);
  assert.equal(publicIP('1.1.1.1'),true);
  assert.equal(publicIP('2606:4700:4700::1111'),true);
});
test('snapshot normalization discards source, assembly, transcripts, credentials and extra fields',()=>{
  const input={...demo(),source:'PRIVATE',assembly:'PRIVATE',transcripts:['PRIVATE'],token:'PRIVATE'};
  input.functions[0].push('PRIVATE');
  assert.equal(JSON.stringify(snapshot(input,'demo')).includes('PRIVATE'),false);
  assert.throws(()=>snapshot(input,'other'));
  input.summary.matched=99;
  assert.throws(()=>snapshot(input,'demo'));
});
test('streamed response sizes are bounded',async()=>{
  await assert.rejects(()=>readJSON(new Response('x'.repeat(100)),20),/too large/);
});
test('DNS private answers and redirects are refused without following them',async()=>{
  let called=0;
  await assert.rejects(()=>publicFetch('https://room.example','/directory/project',async()=>{called++;return Response.json({Answer:[{type:1,data:'127.0.0.1'}]});}),/public internet/);
  assert.equal(called,2);
  await assert.rejects(()=>publicFetch('https://room.example','/directory/project',async(url,options)=>{
    if(url.includes('dns-query')) return dns();
    assert.equal(options.redirect,'manual');
    assert.equal(options.headers.Authorization,undefined);
    return new Response('',{status:302,headers:{Location:'http://127.0.0.1'}});
  }),/unavailable/);
});
test('publishing requires host proof, preserves only public fields, and removal requires owner key',async()=>{
  const env=environment(); const previous=globalThis.fetch; let proof='wrong';
  globalThis.fetch=async url=>url.includes('dns-query')?dns():Response.json({...demo(),proof,source:'PRIVATE'});
  try {
    const registration=await (await api(post('/api/registrations',{url:'https://room.example/join/open#room=demo'}),env)).json();
    await assert.rejects(()=>api(post('/api/publish',{id:registration.id}),env),/publishing code/);
    proof=registration.challenge;
    const published=await (await api(post('/api/publish',{id:registration.id}),env)).json();
    assert.ok(published.ownerKey);
    const listing=await (await api(new Request('https://directory.example/api/projects'),env)).json();
    assert.equal(listing.projects.length,1);
    assert.equal(JSON.stringify([...env.objects.values()]).includes('PRIVATE'),false);
    const project=await (await api(new Request('https://directory.example/api/projects/'+published.id),env)).json();
    assert.equal(project.snapshot.functions.length,2);
    assert.equal(JSON.stringify(project).includes(proof),false);
    const denied=await api(new Request('https://directory.example/api/projects/'+published.id,{method:'DELETE'}),env);
    assert.equal(denied.status,403);
    const removed=await api(new Request('https://directory.example/api/projects/'+published.id,{method:'DELETE',headers:{Authorization:'Bearer '+published.ownerKey}}),env);
    assert.equal(removed.status,200);assert.equal(env.objects.size,0);
  } finally {globalThis.fetch=previous;}
});
test('offline hosts retain last good snapshot and back off',async()=>{
  const env=environment();const previous=globalThis.fetch;
  env.db.prepare('INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)').run('a'.repeat(32),'https://room.example','demo','Demo','Demo','{}','rev','owner',1,1,1,0,0);
  env.objects.set('a'.repeat(32)+'/rev.json',JSON.stringify(demo()));
  globalThis.fetch=async()=>{throw new Error('offline');};
  try{await worker.scheduled({},env);assert.equal(env.db.prepare('SELECT failures FROM projects').get().failures,1);assert.equal(env.objects.size,1);}finally{globalThis.fetch=previous;}
});
test('withdrawal removes cached metadata on the next successful host check',async()=>{
  const env=environment();const previous=globalThis.fetch;
  env.db.prepare('INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)').run('a'.repeat(32),'https://room.example','demo','Demo','Demo','{}','rev','owner',1,1,1,0,0);
  env.objects.set('a'.repeat(32)+'/rev.json',JSON.stringify(demo()));
  globalThis.fetch=async url=>url.includes('dns-query')?dns():new Response('',{status:410});
  try{await worker.scheduled({},env);assert.equal(env.db.prepare('SELECT count(*) AS n FROM projects').get().n,0);assert.equal(env.objects.size,0);}finally{globalThis.fetch=previous;}
});
test('expired publishing codes and excessive registration attempts are rejected',async()=>{
  const env=environment();
  const draft=await (await api(post('/api/registrations',{url:'https://room.example'}),env)).json();
  env.db.exec('UPDATE registrations SET expires=0');
  await assert.rejects(()=>api(post('/api/publish',{id:draft.id}),env),/expired/);
  env.db.exec('UPDATE rate_limits SET count=30');
  await assert.rejects(()=>api(post('/api/registrations',{url:'https://room.example'}),env),/Too many/);
});
