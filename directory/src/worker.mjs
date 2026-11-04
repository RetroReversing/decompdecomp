import { roomAddress, publicIP, snapshot, readJSON } from './protocol.mjs';

const now = () => Math.floor(Date.now() / 1000);
const random = () => crypto.randomUUID().replaceAll('-', '') + crypto.randomUUID().replaceAll('-', '');
const hash = async value => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))), b => b.toString(16).padStart(2, '0')).join('');
const json = (data, status = 200, cache = 'no-store') => Response.json(data, {status, headers: {
  'Cache-Control': cache, 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'
}});
const fail = message => { throw new Error(message); };

/** Revalidate on every fetch, refuse redirects, and never forward credentials or invite codes. */
export async function publicFetch(origin, path, fetcher = fetch) {
  const host = new URL(origin).hostname;
  const answers = await Promise.all(['A', 'AAAA'].map(async type => {
    const result = await fetcher(`https://cloudflare-dns.com/dns-query?name=${encodeURIComponent(host)}&type=${type}`, {
      headers: {accept: 'application/dns-json'}, signal: AbortSignal.timeout(5000)
    });
    if (!result.ok) fail('Could not check the public hostname.');
    return (await readJSON(result, 16384)).Answer || [];
  }));
  const addresses = answers.flat().filter(answer => [1,28].includes(answer.type));
  if (!addresses.length || addresses.some(answer => !publicIP(answer.data))) fail('The host must resolve to public internet addresses.');
  const response = await fetcher(origin + path, {redirect: 'manual', signal: AbortSignal.timeout(10000),
    headers: {accept: 'application/json', 'User-Agent': 'decompdecomp-directory/1'}});
  if (response.status === 410) throw Object.assign(new Error('The host withdrew public metadata.'), {withdrawn: true});
  if (!response.ok) fail('The host is unavailable or public metadata is disabled.');
  return readJSON(response);
}
async function rate(env, request) {
  const key = await hash((request.headers.get('CF-Connecting-IP') || 'local') + ':' + Math.floor(now() / 86400));
  const row = await env.DB.prepare('INSERT INTO rate_limits(key,count,expires) VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1 RETURNING count')
    .bind(key, now()+86400).first();
  if (row.count > 30) fail('Too many publishing requests today. Try again tomorrow.');
}
async function input(request) {
  if (!request.headers.get('Content-Type')?.startsWith('application/json')) fail('Send JSON.');
  return readJSON(request, 4096);
}
async function hostSnapshot(origin, room) {
  return publicFetch(origin, '/directory/project' + (room ? '?room=' + encodeURIComponent(room) : ''));
}
function listing(row) {
  return {id: row.id, title: row.title, platform: row.platform, summary: JSON.parse(row.summary),
    checked_at: row.checked_at, success_at: row.success_at, online: row.failures === 0,
    join: row.origin + '/join/open#room=' + encodeURIComponent(row.room)};
}
async function save(env, id, origin, room, raw, ownerHash = null) {
  const clean = snapshot(raw, room);
  const serialized = JSON.stringify(clean);
  const revision = await hash(serialized);
  const key = `${id}/${revision}.json`;
  // Immutable objects ensure a reader sees the snapshot named by its database revision.
  const old = await env.DB.prepare('SELECT revision FROM projects WHERE id=?').bind(id).first();
  if (!old || old.revision !== revision) {
    await env.SNAPSHOTS.put(key, serialized, {httpMetadata: {contentType: 'application/json'}});
  }
  if (ownerHash) {
    await env.DB.prepare('INSERT INTO projects(id,origin,room,title,platform,summary,revision,owner_hash,published_at,checked_at,success_at,next_check) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)')
      .bind(id, origin, room, clean.title, clean.platform, JSON.stringify(clean.summary), revision, ownerHash, now(), now(), now(), now()+1800).run();
  } else {
    await env.DB.prepare('UPDATE projects SET title=?,platform=?,summary=?,revision=?,checked_at=?,success_at=?,next_check=?,failures=0 WHERE id=?')
      .bind(clean.title, clean.platform, JSON.stringify(clean.summary), revision, now(), now(), now()+1800, id).run();
  }
  if (old && old.revision !== revision) await env.SNAPSHOTS.delete(`${id}/${old.revision}.json`);
  return clean;
}
async function refresh(env, row) {
  try {
    const raw = await hostSnapshot(row.origin, row.room);
    await save(env, row.id, row.origin, row.room, raw);
  } catch (error) {
    if (error.withdrawn) {
      await env.DB.prepare('DELETE FROM projects WHERE id=?').bind(row.id).run();
      await env.SNAPSHOTS.delete(`${row.id}/${row.revision}.json`);
      return;
    }
    const delay = Math.min(86400, 1800 * 2 ** Math.min(row.failures + 1, 6));
    await env.DB.prepare('UPDATE projects SET checked_at=?,next_check=?,failures=failures+1 WHERE id=?')
      .bind(now(), now()+delay, row.id).run();
  }
}
export async function api(request, env) {
  const url = new URL(request.url);
  const path = url.pathname;
  if (request.method === 'GET' && path === '/api/projects') {
    const page = Math.max(0, Math.min(10000, Number(url.searchParams.get('page')) || 0)) | 0;
    const search = (url.searchParams.get('q') || '').slice(0,100);
    const rows = await env.DB.prepare('SELECT * FROM projects WHERE title LIKE ? ORDER BY title,id LIMIT 51 OFFSET ?')
      .bind('%'+search+'%', page*50).all();
    return json({projects: rows.results.slice(0,50).map(listing), more: rows.results.length > 50}, 200, 'public, max-age=60');
  }
  const match = path.match(/^\/api\/projects\/([a-f0-9]{32})$/);
  if (match && request.method === 'GET') {
    const row = await env.DB.prepare('SELECT * FROM projects WHERE id=?').bind(match[1]).first();
    if (!row) return json({error: 'Project not found.'}, 404);
    const object = await env.SNAPSHOTS.get(`${row.id}/${row.revision}.json`);
    if (!object) return json({error: 'Snapshot unavailable. Try again shortly.'}, 503);
    return json({...listing(row), snapshot: await object.json()}, 200, 'public, max-age=60');
  }
  if (request.method === 'POST' && path === '/api/registrations') {
    await rate(env, request);
    const {origin, room} = roomAddress((await input(request)).url);
    const id = crypto.randomUUID().replaceAll('-', '');
    const challenge = random();
    await env.DB.prepare('INSERT INTO registrations(id,origin,room,challenge,expires) VALUES(?,?,?,?,?)')
      .bind(id,origin,room,challenge,now()+900).run();
    return json({id, challenge});
  }
  if (request.method === 'POST' && path === '/api/publish') {
    await rate(env, request);
    const data = await input(request);
    const draft = await env.DB.prepare('SELECT * FROM registrations WHERE id=? AND expires>?').bind(String(data.id),now()).first();
    if (!draft) fail('The publishing code expired. Start again.');
    const raw = await hostSnapshot(draft.origin, draft.room);
    if (raw.proof !== draft.challenge) fail('Paste this publishing code into the host app and save it before publishing.');
    const room = draft.room || raw.room;
    if (typeof room !== 'string' || !/^[a-zA-Z0-9_-]{1,100}$/.test(room)) fail('Choose a specific room link.');
    if (await env.DB.prepare('SELECT id FROM projects WHERE origin=? AND room=?').bind(draft.origin,room).first()) fail('This room is already listed.');
    const ownerKey = random();
    await save(env, draft.id, draft.origin, room, raw, await hash(ownerKey));
    await env.DB.prepare('DELETE FROM registrations WHERE id=?').bind(draft.id).run();
    return json({id: draft.id, ownerKey});
  }
  if (match && request.method === 'DELETE') {
    await rate(env, request);
    const ownerKey = request.headers.get('Authorization')?.replace(/^Bearer /, '') || '';
    const row = await env.DB.prepare('SELECT * FROM projects WHERE id=? AND owner_hash=?').bind(match[1],await hash(ownerKey)).first();
    if (!row) return json({error: 'Use the removal key saved when this project was published.'},403);
    await env.DB.prepare('DELETE FROM projects WHERE id=?').bind(row.id).run();
    await env.SNAPSHOTS.delete(`${row.id}/${row.revision}.json`);
    return json({removed: true});
  }
  return json({error: 'Not found.'},404);
}
export default {
  async fetch(request, env) {
    if (!new URL(request.url).pathname.startsWith('/api/')) return env.ASSETS.fetch(request);
    try { return await api(request, env); }
    catch (error) { return json({error: error.message || 'Request failed. Try again.'},400); }
  },
  async scheduled(controller, env) {
    await env.DB.batch([
      env.DB.prepare('DELETE FROM registrations WHERE expires<?').bind(now()),
      env.DB.prepare('DELETE FROM rate_limits WHERE expires<?').bind(now())
    ]);
    // Bounded batches keep free-tier scheduled work small. Larger directories refresh less frequently.
    const rows = await env.DB.prepare('SELECT * FROM projects WHERE next_check<=? ORDER BY next_check LIMIT 5').bind(now()).all();
    for (const row of rows.results) await refresh(env, row);
  }
};
