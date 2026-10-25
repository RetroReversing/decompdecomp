"""ROM header hints and evidence from explicitly imported local libultra archives.

Library fingerprints identify code, not a whole SDK release: unchanged objects occur in many releases.
No archive is downloaded or executed by this module.
"""

import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..network import NetworkError
from .function_names import record, state

_VERSION = re.compile(r"\blib(?:g)?ultra\s+(2\.0[A-L])\b", re.I)
log = logging.getLogger("decompdecomp")
MIN_BYTES = 32
# Giving a symbol a compiler builtin's spelling can change calls or reject existing prototypes.
BUILTIN_NAMES = frozenset("memcpy memmove memset memcmp memchr strlen strchr strrchr strcpy strncpy strcmp "
                          "strncmp strcat strncat strstr sqrt sqrtf sin sinf cos cosf tan tanf fabs fabsf "
                          "abs labs malloc calloc realloc free printf sprintf snprintf puts putchar exit abort".split())


def report(net, proj):
    cfg = net.cfg(proj)
    if cfg.get("platform", "n64") != "n64" or not net.is_rom(proj) or net.fork_slug(proj["slug"]):
        return None
    try:
        with (Path(proj["root"]) / cfg["rom"]["file"]).open("rb") as stream:
            header = stream.read(16)
    except (OSError, KeyError):
        return None
    if len(header) < 16 or header[:4] != bytes.fromhex("80371240"):
        return None
    raw = header[12:16]
    value, letter = header[14], header[15]
    version = f"{value // 10}.{value % 10}{chr(letter)}" if value and 65 <= letter <= 90 else None
    prints = net.twins.prints(proj)
    keys = Counter(p.key for p in prints.values() if p.size >= MIN_BYTES and not p.asm)
    corpora = defaultdict(set)
    names = defaultdict(set)
    versions = defaultdict(set)
    for row in net.store.all("SELECT corpus,name,fingerprint FROM sdk_signatures WHERE arch='mips' AND big_endian=1"):
        match = _VERSION.search(row["corpus"])
        if not match or row["fingerprint"] not in keys:
            continue
        versions[row["fingerprint"]].add(match.group(1).upper())
        corpora[row["corpus"]].add(row["fingerprint"])
        names[row["corpus"]].add(row["name"])
    candidates = [{"corpus": c, "version": _VERSION.search(c).group(1).upper(),
                   "fingerprints": len(ks), "version_specific_fingerprints": sum(len(versions[k]) == 1 for k in ks),
                   "functions": sum(keys[k] for k in ks), "names": len(names[c])}
                  for c, ks in corpora.items()]
    candidates.sort(key=lambda r: (-r["fingerprints"], r["corpus"]))
    return {"header_version": version, "header_raw": raw.hex(), "header_offset": "0x0C",
            "signature_candidates": candidates,
            "note": "Header declaration; signature counts are supporting evidence, not proof of one SDK revision."}


def name_functions(net, slug, job=None):
    """Name unclaimed generated functions from unambiguous full-size local library fingerprints.

    All imported libultra variants participate in ambiguity checks. No decompilation match is awarded.
    """
    proj = net.project_row(slug)
    if slug in net.links or not net.is_rom(proj) or net.fork_slug(slug) or net.cfg(proj).get("platform", "n64") != "n64":
        return 0
    donors = defaultdict(lambda: defaultdict(set))
    for r in net.store.all("SELECT corpus,name,fingerprint,size FROM sdk_signatures WHERE arch='mips' AND big_endian=1"):
        if _VERSION.search(r["corpus"]) and r["size"] >= MIN_BYTES and re.fullmatch(r"[A-Za-z_]\w*", r["name"]):
            donors[r["fingerprint"]][r["name"]].add(r["corpus"])
    prints = net.twins.prints(proj)
    rows = net.store.all("SELECT * FROM functions WHERE project_id=?", proj["id"])
    taken = {r["name"] for r in rows} | set(net.symbols(proj["id"]).by_name)
    candidates = []
    multiplicity = Counter(p.key for p in prints.values())
    for row in rows:
        if job is not None and job.stop.is_set():
            return 0
        p = prints.get(row["id"])
        naming = state(row)
        choices = donors.get(p.key, {}) if p and not p.asm and p.size >= MIN_BYTES else {}
        if naming["named"] or naming["renamed"] or len(choices) != 1 or multiplicity[p.key] != 1:
            continue
        name, corpora = next(iter(choices.items()))
        if name in BUILTIN_NAMES or name in taken or net.store.one("SELECT 1 FROM claims WHERE function_id=?", row["id"]):
            continue
        candidates.append((row, name, sorted(corpora), p.key))
    counts = Counter(n for _, n, _, _ in candidates)
    candidates = [(r, n, c, k) for r, n, c, k in candidates if counts[n] == 1][:128]
    if not candidates:
        return 0
    agent, _ = net.local_agent("autopilot")
    try:
        result = net.rename_symbols(agent, slug, [(r["name"], name) for r, name, _, _ in candidates],
                                    "Names inferred from exact local libultra archive signatures")
    except (NetworkError, OSError, ValueError):
        return 0
    if not result.startswith("✅"):
        log.info("libultra names for %s rejected: %s", slug, result[:1500])
        return 0
    for row, name, corpora, key in candidates:
        evidence = (f"Exact relocation-normalized {row['size']}-byte function fingerprint {key}; "
                    f"one name across imported libultra signatures, unique target fingerprint. Archives: "
                    + "; ".join(corpora) + ". Name inferred from library code; SDK revision not proven.")
        with net.store.tx() as db:
            record(db, row["id"], row["name"], name, method="sdk_signature", source_corpora=corpora,
                   source_name=name, fingerprint=key, evidence=evidence)
        net.add_note(agent, slug, evidence, name)
    return len(candidates)
