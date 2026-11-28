"""Recover consecutive ERZ program chunks corroborated by captured runtime memory."""
import re

from decompassets import rnc

_CHUNK = 0x10000
_MAX_OUTPUT = 4 * 1024 * 1024
_WINDOW = 4096


def decode(rom, row):
    """Recreate a bounded, contiguous chunk run from the original cartridge."""
    if not isinstance(row, dict):
        return None
    chunks = row.get('chunks')
    if not isinstance(chunks, list) or not 3 <= len(chunks) <= 64:
        return None
    out = bytearray()
    previous = None
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            return None
        offset, extent, size = (chunk.get(key) for key in ('offset', 'extent', 'size'))
        if (any(type(value) is not int for value in (offset, extent, size))
                or offset < 0 or extent < 18 or offset + extent > len(rom)
                or not 0 < size <= _CHUNK or index < len(chunks)-1 and size != _CHUNK
                or len(out) + size > _MAX_OUTPUT or rom[offset:offset+4] != b'ERZ\x02'):
            return None
        if previous is not None and (offset != ((previous+3)&~3) or any(rom[previous:offset])):
            return None
        try:
            payload, used = rnc.unpack_at(rom, offset, max_output=_CHUNK, max_packed=_MAX_OUTPUT)
        except rnc.RncError:
            return None
        if used != extent or len(payload) != size:
            return None
        out.extend(payload)
        previous = offset + extent
    if (chunks[-1]['size'] == _CHUNK or row.get('offset') != chunks[0]['offset'] or row.get('end') != previous
            or row.get('size') != len(out)):
        return None
    return bytes(out)


def find(rom, dumps, targets, occupied=(), check=lambda: None):
    """Require a called entry, consecutive checksummed chunks and independent RAM matches."""
    if not targets or not dumps:
        return []
    found = []
    continuations = set()
    for number, match in enumerate(re.finditer(b'ERZ\x02', rom)):
        if not number % 32:
            check()
        start = match.start()
        if start % 4 or start in continuations:
            continue
        chunks = []
        at = start
        total = 0
        while len(chunks) < 64 and at + 18 <= len(rom) and rom[at:at+4] == b'ERZ\x02':
            check()
            try:
                payload, extent = rnc.unpack_at(rom, at, max_output=_CHUNK, max_packed=_MAX_OUTPUT)
            except rnc.RncError:
                break
            size = len(payload)
            if not size or total + size > _MAX_OUTPUT:
                break
            chunks.append(dict(offset=at, extent=extent, size=size))
            total += size
            end = at + extent
            following = (end+3)&~3
            if size < _CHUNK or following > len(rom) or any(rom[end:following]):
                break
            at = following
        if len(chunks) < 3 or chunks[-1]['size'] == _CHUNK:
            continue
        continuations.update(chunk['offset'] for chunk in chunks[1:])
        row = dict(codec='chunked_erz', offset=start, end=chunks[-1]['offset']+chunks[-1]['extent'],
                   size=total, chunks=chunks)
        payload = decode(rom, row)
        if payload is None:
            continue
        placements = {}
        for ram in dumps:
            for target in targets:
                pos = target & 0x1fffffff
                if pos + 64 > len(ram):
                    continue
                needle = ram[pos:pos+64]
                where = payload.find(needle)
                if where < 0 or payload.find(needle, where+1) >= 0:
                    continue
                base = target-where
                physical = base & 0x1fffffff
                if (base % 16 or not 0x80000400 <= base < base+len(payload) <= 0x80800000
                        or physical+len(payload) > len(ram)
                        or any(v < base+len(payload) and base < v+hi-lo for lo, hi, v in occupied)):
                    continue
                cursor = complete = 0
                for chunk in chunks:
                    size = chunk['size']
                    body = payload[cursor:cursor+size]
                    captured = ram[physical+cursor:physical+cursor+size]
                    complete += body == captured
                    windows = sum(body[i:i+_WINDOW] == captured[i:i+_WINDOW]
                                  and len(set(body[i:i+_WINDOW])) >= 32
                                  for i in range(0, size-_WINDOW+1, _WINDOW))
                    if windows < 2:
                        break
                    cursor += size
                if cursor == len(payload) and complete >= 3:
                    placements.setdefault(base, set()).add(target)
        for base, calls in placements.items():
            found.append({**row, 'vram': base, 'calls': sorted(calls)})
    return found if len(found) == 1 else []
