"""A chunked executable needs checked bytes and independent runtime evidence."""
import random
import struct

import pytest

from decompassets import rnc
from decompnet.consoles.n64 import runtime_erz_chunks, runtime_zlib


def erz_literal(raw):
    data = bytearray()
    position = 8
    index = 0

    def bit(value):
        nonlocal position, index
        if position == 8:
            index = len(data)
            data.append(0)
            position = 0
        data[index] |= value << (7-position)
        position += 1

    bit(0); bit(0)
    for byte in raw:
        bit(0)
        data.append(byte)
    for value in (1, 1, 1, 1):
        bit(value)
    data.append(0)
    bit(0)
    payload = bytes(data)
    return b'ERZ\x02' + struct.pack('>IIHHBB', len(raw), len(payload), rnc.crc16(raw),
                                     rnc.crc16(payload), 0, 1) + payload


@pytest.fixture
def example(monkeypatch):
    monkeypatch.setattr(runtime_erz_chunks, '_CHUNK', 8192)
    monkeypatch.setattr(runtime_erz_chunks, '_WINDOW', 512)
    rng = random.Random(47)
    parts = [rng.randbytes(n) for n in (8192, 8192, 8192, 4096)]
    rom = bytearray(0x1000)
    for part in parts:
        rom.extend(erz_literal(part))
        rom.extend(bytes(-len(rom) % 4))
    base = 0x80001000
    ram = bytearray(0x10000)
    payload = b''.join(parts)
    ram[0x1000:0x1000+len(payload)] = payload
    return bytes(rom), bytes(ram), payload, base


def test_chunks_require_runtime_code_and_validate_the_original_stream(example):
    rom, ram, payload, base = example
    target = base+0x2100
    rows = runtime_erz_chunks.find(rom, [ram], {target})
    assert len(rows) == 1 and rows[0]['vram'] == base
    assert runtime_erz_chunks.decode(rom, rows[0]) == payload
    expanded = rom + bytes(-len(rom) % 16+16) + payload
    expanded += bytes(-len(expanded) % 16)
    assert runtime_zlib.verify_analysis(rom, expanded, rows)
    broken = bytearray(rom)
    broken[rows[0]['chunks'][1]['offset']+20] ^= 1
    assert runtime_erz_chunks.decode(broken, rows[0]) is None
    assert not runtime_zlib.verify_analysis(broken, expanded, rows)
    assert not runtime_erz_chunks.find(rom, [ram], set())
    assert not runtime_erz_chunks.find(rom, [ram], {target}, [(0, len(payload), base)])


def test_partial_runtime_changes_need_independent_matching_windows(example):
    rom, ram, payload, base = example
    target = base+0x2100
    changed = bytearray(ram)
    changed[0x1000+3*8192+20] ^= 1
    assert runtime_erz_chunks.find(rom, [changed], {target})
    changed[0x1000+3*8192:0x1000+3*8192+len(payload)-3*8192] = bytes(4096)
    assert not runtime_erz_chunks.find(rom, [changed], {target})


def test_competing_runtime_placements_and_cancellation_do_not_choose_a_program(example):
    rom, ram, payload, base = example
    doubled = bytearray(0x18000)
    doubled[:len(ram)] = ram
    doubled[0x9000:0x9000+len(payload)] = payload
    assert not runtime_erz_chunks.find(rom, [doubled], {base+0x2100, base+0x2100+0x8000})

    def stop():
        raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        runtime_erz_chunks.find(rom, [ram], {base+0x2100}, check=stop)
