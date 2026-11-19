"""An Xbox 360 image's own function table (.pdata), and the base-image layout it tells apart.

Synthetic PE images only; the real checks were made by hand on two games' images: .pdata listed 27,923 and 104,828
functions, 98-99% beginning with mflr r12 and every one ending on blr, a tail-call b or a call that never returns.
"""

import struct

from decompnet.consoles.xbox360 import pdata, scratch

BASE = 0x82000000
MFLR = 0x7D8802A6
BLR = 0x4E800020
TEXT_VA, TEXT_RAW = 0x2000, 0x1A00  # .text's address and the file offset the section table gives (like XexTool's)
PDATA_VA = 0x1000
IMAGE_SIZE = 0x4000


def _pe(sections, image_size=IMAGE_SIZE, header_size=0x400) -> bytearray:
    """A PowerPC PE header with `sections` [(name, va, vsize, raw, rsize, flags)]; the rest is up to the caller."""
    out = bytearray(header_size)
    out[0:2] = b"MZ"
    struct.pack_into("<I", out, 0x3C, 0x80)
    out[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", out, 0x84, scratch.PE_MACHINE_PPC, len(sections))
    struct.pack_into("<H", out, 0x94, 0xE0)  # SizeOfOptionalHeader
    opt = 0x98
    struct.pack_into("<I", out, opt + 16, TEXT_VA)  # entry
    struct.pack_into("<I", out, opt + 28, BASE)
    struct.pack_into("<II", out, opt + 56, image_size, header_size)
    for index, (name, va, vsize, raw, rsize, flags) in enumerate(sections):
        at = opt + 0xE0 + index * 40
        out[at:at + 8] = name.encode().ljust(8, b"\0")
        struct.pack_into("<IIII", out, at + 8, vsize, va, rsize, raw)
        struct.pack_into("<I", out, at + 36, flags)
    return out


SECTIONS = [(".pdata", PDATA_VA, 0x18, PDATA_VA, 0x200, 0x40000000),
            (".text", TEXT_VA, 0x100, TEXT_RAW, 0x200, 0x60000020)]
FUNCS = [(BASE + TEXT_VA, 0x10), (BASE + TEXT_VA + 0x10, 0x20), (BASE + TEXT_VA + 0x30, 0x8)]


def _table() -> bytes:
    return b"".join(struct.pack(">II", start, (length // 4) << 8 | 2) for start, length in FUNCS)


def _code() -> bytes:
    code = bytearray(0x100)
    for start, length in FUNCS:
        at = start - BASE - TEXT_VA
        struct.pack_into(">I", code, at, MFLR)
        struct.pack_into(">I", code, at + length - 4, BLR)
    return bytes(code)


def memory_layout_image() -> bytes:
    """What XexTool writes: every section at its address, the section table still naming file offsets."""
    image = _pe(SECTIONS)
    image.extend(bytes(IMAGE_SIZE - len(image)))
    image[PDATA_VA:PDATA_VA + 0x18] = _table()
    image[TEXT_VA:TEXT_VA + 0x100] = _code()
    return bytes(image)


def file_layout_image() -> bytes:
    """A real file layout: each section at the file offset its table entry gives."""
    image = _pe(SECTIONS)
    image.extend(bytes(TEXT_RAW + 0x200 - len(image)))
    image[PDATA_VA:PDATA_VA + 0x18] = _table()
    image[TEXT_RAW:TEXT_RAW + 0x100] = _code()
    return bytes(image)


def test_the_table_lists_each_function_with_its_length():
    assert pdata.entries(_table()) == FUNCS
    assert pdata.entries(_table() + bytes(8)) == FUNCS  # empty entries are not functions


def test_a_xextool_image_is_read_in_memory_layout_and_a_real_file_one_in_file_layout():
    assert pdata.memory_layout(memory_layout_image()) is True
    assert pdata.memory_layout(file_layout_image()) is False


def test_a_memory_layout_base_image_maps_its_code_at_its_address():
    """Mapped as a file layout, XexTool's .text landed (text va - raw) bytes away: every target was cut wrong."""
    mapped = scratch.read_base_image(memory_layout_image())["image"]
    assert struct.unpack_from(">I", mapped, TEXT_VA)[0] == MFLR
    assert pdata.functions(mapped, BASE, log=lambda line: None) == FUNCS


def test_a_file_layout_base_image_still_maps_by_its_section_table_and_keeps_its_header():
    mapped = scratch.read_base_image(file_layout_image())["image"]
    assert struct.unpack_from(">I", mapped, TEXT_VA)[0] == MFLR
    assert mapped[:2] == b"MZ", "the header is mapped too: it is how .pdata is found"
    assert pdata.functions(mapped, BASE, log=lambda line: None) == FUNCS


def test_a_table_that_does_not_describe_the_code_is_not_trusted():
    """A mis-mapped image's table names other bytes; cutting functions there would make every target wrong."""
    shifted = bytearray(memory_layout_image())
    shifted[TEXT_VA:TEXT_VA + 0x100] = bytes(0x100)
    shifted[TEXT_VA + 0x5E0:TEXT_VA + 0x5E0 + 0x100] = _code()  # the code somewhere else
    said = []
    assert pdata.functions(bytes(shifted), BASE, log=said.append) is None
    assert said and "doesn't match its code" in said[0]


def test_overlapping_or_out_of_code_entries_are_left_out():
    image = bytearray(memory_layout_image())
    extra = struct.pack(">II", BASE + TEXT_VA + 0x14, (0x8 // 4) << 8) + struct.pack(">II", BASE + 0x10, 1 << 8)
    image[PDATA_VA:PDATA_VA + 0x28] = _table() + extra
    struct.pack_into("<I", image, 0x98 + 0xE0 + 8, 0x28)  # .pdata's vsize now covers them
    assert pdata.functions(bytes(image), BASE, log=lambda line: None) == FUNCS


def test_analysis_fills_gaps_lends_names_and_its_fragments_are_dropped():
    table = [{"name": f"FUN_{a:08x}", "addr": a, "size": n} for a, n in FUNCS]
    analysed = [
        {"name": "FUN_82002000", "addr": BASE + TEXT_VA, "size": 0x8},  # same start: the table's length stands
        {"name": "Update", "addr": BASE + TEXT_VA + 0x10, "size": 0x20},  # same start, a real name: taken
        {"name": "FUN_82002018", "addr": BASE + TEXT_VA + 0x18, "size": 0x8},  # inside a table function: a piece
        {"name": "FUN_82002040", "addr": BASE + TEXT_VA + 0x40, "size": 0x10},  # past every table function: a leaf
        {"name": "FUN_82002034", "addr": BASE + TEXT_VA + 0x34, "size": 0x10},  # runs into the next start: dropped
    ]
    merged, added, dropped = pdata.merge(table, analysed)
    assert [(row["addr"] - BASE, row["size"], row["name"]) for row in merged] == [
        (TEXT_VA, 0x10, "FUN_82002000"), (TEXT_VA + 0x10, 0x20, "Update"), (TEXT_VA + 0x30, 0x8, "FUN_82002030"),
        (TEXT_VA + 0x40, 0x10, "FUN_82002040")]
    assert (added, dropped) == (1, 2)
    assert pdata.merge(table, None) == (table, 0, 0)


def test_the_import_takes_its_functions_from_the_table_without_ghidra(tmp_path, monkeypatch):
    """With no function map and no Ghidra, the import used to fail; the image's own table is enough."""
    import json

    from decompnet.forks import ghidra

    game = tmp_path / "Game.bin"
    game.write_bytes(memory_layout_image())
    monkeypatch.setattr(ghidra, "available", lambda: False)
    monkeypatch.setattr(scratch, "_run", lambda cmd, root, what, log: (True, ""))
    logs = []
    root = tmp_path / "project"
    try:
        scratch.create(root, game, log=logs.append)
    except Exception:  # noqa: BLE001  a later build step may refuse a synthetic game; what create() wrote counts
        pass
    cfg = json.loads(next((root / "config").glob("*/functions.json")).read_text(encoding="utf-8"))
    got = sorted((fn["addr"], fn["size"], fn["name"]) for fns in cfg["units"].values() for fn in fns)
    assert got == [(a, n, f"FUN_{a:08x}") for a, n in FUNCS]
    assert any("function table (.pdata) lists 3 functions" in line for line in logs)
    assert any("small leaf functions" in line for line in logs), "what is missing without Ghidra is said"
