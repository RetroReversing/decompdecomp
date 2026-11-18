/* The "add a game" page classifies a picked file from the browser, before any upload: an Xbox 360 disc's video
   partition is ISO 9660 ("CD001" at 0x8001), so it was shown as a PlayStation disc and never reached the server.
   These check the disc probe that runs first. */
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync("src/decompnet/app/static/js/home.js", "utf8");
const start = source.indexOf("async function identifyXboxDisc(");
const end = source.indexOf("\nfunction identifyHead(");
if (start < 0 || end < 0) throw new Error("identifyXboxDisc not found in home.js");
const env = {String, Uint8Array, DataView};
vm.createContext(env);
vm.runInContext(`${source.slice(start, end)}\nthis.identifyXboxDisc = identifyXboxDisc;`, env);

const MAGIC = new TextEncoder().encode("MICROSOFT*XBOX*MEDIA");

/* A file whose bytes are the given writes ({offset: bytes}), zero elsewhere. */
function disc(size, writes) {
  return {
    name: "Teenage Mutant Ninja Turtles.iso", size,
    slice(from, to) {
      const end = Math.min(to, size), out = new Uint8Array(Math.max(0, end - from));
      for (const [at, bytes] of Object.entries(writes)) {
        const off = Number(at), first = Math.max(from, off), last = Math.min(end, off + bytes.length);
        if (last > first) out.set(bytes.subarray(first - off, last - off), first - from);
      }
      return {arrayBuffer: async () => out.buffer};
    },
  };
}

/* A volume descriptor naming a root directory whose first entry is `boot`. */
function discWrites(descriptorAt, boot) {
  const sector = 33, writes = {};
  const head = new Uint8Array(0x20);
  head.set(MAGIC, 0);
  new DataView(head.buffer).setUint32(0x14, sector, true);
  writes[descriptorAt] = head;
  const entry = new Uint8Array(14 + boot.length);
  entry[14 - 1] = boot.length;
  entry.set(new TextEncoder().encode(boot), 14);
  writes[sector * 2048] = entry;
  return writes;
}

(async () => {
  // A full Xbox 360 dump: the game partition starts at a fixed offset per generation.
  for (const base of [0xFD90000, 0x2080000]) {
    const found = await env.identifyXboxDisc(disc(9 * 1024 ** 3, discWrites(base + 0x10000, "Default.xex")));
    assert.equal(found.platform, "xbox360", `Xbox 360 disc at ${base.toString(16)}`);
    assert.equal(found.order, "Xbox 360 disc image");
  }

  // A rip of only the game partition: the boot executable's name tells the two consoles apart.
  const xbox360 = await env.identifyXboxDisc(disc(7 * 1024 ** 3, discWrites(0x10000, "default.xex")));
  assert.equal(xbox360.platform, "xbox360");
  const xbox = await env.identifyXboxDisc(disc(4 * 1024 ** 3, discWrites(0x10000, "default.xbe")));
  assert.equal(xbox.platform, "xbox");

  // A PlayStation disc is still a PlayStation disc: no XDVDFS anywhere.
  assert.equal(await env.identifyXboxDisc(disc(700 * 1024 ** 2, {})), null);
  console.log("ok - xbox 360 discs are recognised before the ISO 9660 checks");
})();
