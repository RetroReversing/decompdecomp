/** The directory accepts a deliberately small public contract, never arbitrary app state. */
export const MAX_SNAPSHOT = 4 * 1024 * 1024;
export const MAX_FUNCTIONS = 20000;
const STATUSES = new Set(['open', 'working', 'matched', 'asm', 'not_c', 'set_aside']);
export function roomAddress(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.port ||
      !/^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$/i.test(url.hostname) ||
      /(^|\.)(localhost|local|internal|test|invalid|onion)$/.test(url.hostname)) {
    throw new Error('Use a public HTTPS room address, without a port or credentials.');
  }
  if (url.pathname !== '/' && url.pathname !== '/join/open') {
    throw new Error('Use a public room link ending in /join/open, or its HTTPS hostname.');
  }
  const room = new URLSearchParams(url.hash.slice(1)).get('room') || url.searchParams.get('room') || '';
  if (room && !/^[a-zA-Z0-9_-]{1,100}$/.test(room)) throw new Error('Invalid room name.');
  return { origin: url.origin, room };
}
export function publicIP(ip) {
  if (ip.includes(':')) {
    // Public IPv6 global unicast only. IPv4-mapped and local addresses are excluded.
    return /^[23][0-9a-f]{0,3}:/i.test(ip) && !/^2001:(db8|0:|10:|20:)/i.test(ip);
  }
  const parts = ip.split('.');
  if (parts.length !== 4 || parts.some(x => !/^\d{1,3}$/.test(x) || Number(x) > 255)) return false;
  const [a,b] = parts.map(Number);
  return !(a === 0 || a === 10 || a === 127 || a >= 224 ||
    (a === 100 && b >= 64 && b <= 127) || (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) || (a === 192 && [0,168].includes(b)) ||
    (a === 198 && [18,19,51].includes(b)) || (a === 203 && b === 0));
}
const number = (value, max = Number.MAX_SAFE_INTEGER) => {
  if (!Number.isSafeInteger(value) || value < 0 || value > max) throw new Error('Invalid numeric metadata.');
  return value;
};
const label = (value, max) => {
  if (typeof value !== 'string' || value.length > max || /[\u0000-\u001f]/.test(value)) throw new Error('Invalid project label.');
  return value;
};
export function snapshot(data, expectedRoom) {
  if (data?.version !== 1 || data.room !== expectedRoom || !Array.isArray(data.functions) || data.functions.length > MAX_FUNCTIONS) {
    throw new Error('The host did not return supported public project metadata. Update the host app.');
  }
  const summary = {
    functions: number(data.summary?.functions), matched: number(data.summary?.matched),
    bytes: number(data.summary?.bytes), matched_bytes: number(data.summary?.matched_bytes)
  };
  if (summary.matched > summary.functions || summary.matched_bytes > summary.bytes || summary.functions !== data.functions.length) {
    throw new Error('The host returned inconsistent project totals.');
  }
  const functions = data.functions.map(row => {
    if (!Array.isArray(row) || !STATUSES.has(row[2])) throw new Error('Invalid function metadata.');
    return [label(row[0], 256), number(row[1]), row[2], row[3] === null ? null : number(row[3]), number(row[4])];
  });
  // Rebuild the object: unknown fields (including source, bytes and transcripts) are never persisted.
  return { version: 1, room: expectedRoom, title: label(data.title, 160), platform: label(data.platform, 80),
    compiler: label(data.compiler, 160), summary, functions };
}
export async function readJSON(response, max = MAX_SNAPSHOT) {
  if (!response.body) throw new Error('The host returned an empty response.');
  const reader = response.body.getReader();
  const chunks = []; let length = 0;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > max) throw new Error('The public snapshot is too large.');
      chunks.push(value);
    }
  } finally { await reader.cancel().catch(() => {}); }
  const bytes = new Uint8Array(length); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));
}
