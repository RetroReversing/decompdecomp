"""Ghidra's drafts for Xbox 360 made into C a PPC compiler accepts, and the console's draft hooks.

Synthetic drafts only: no Ghidra, no Wine, no real game. The real pipeline was checked by hand against a
delinked unit of a real game (13 of 14 drafts compiled with the XDK's cl.exe; the 14th reads bad
instruction data, which no C stands for).
"""

import json
from pathlib import Path
from types import SimpleNamespace

from decompnet.consoles.xbox360 import XBOX360, drafts

DRAFT = """
undefined8 FUN_92179300(long param_1,int *param_2,undefined4 *param_3)

{
  int iVar1;

  iVar1 = func_0x006c31d0(*(undefined4 *)(param_1 + 8));
  if (iVar1 == 0) {
    *param_3 = 1;
  }
  return 0;
}
"""


def test_a_draft_becomes_compilable_c():
    """PPC64's 8-byte long must not become cl.exe's 4-byte one, and the draft needs its types declared."""
    prelude, definition = drafts.compilable(DRAFT, "FUN_92179300")

    assert prelude.startswith('#include "ghidra.h"')
    assert "undefined8 FUN_92179300(longlong param_1," in definition
    assert definition.rstrip().endswith("}")
    assert definition.count("{") == definition.count("}")


def test_what_no_c_stands_for_is_refused():
    """A draft that reads a register or hit bad instruction data would compile to something else entirely."""
    for unwritable in ("in_LR", "in_CTR", "unaff_r31", "halt_baddata", "CONCAT44(a, b)", "stack0x00000010"):
        body = DRAFT.replace("iVar1 = func_0x006c31d0", f"iVar1 = {unwritable}(func_0x006c31d0")
        assert drafts.compilable(body, "FUN_92179300") is None, unwritable


def test_a_known_callee_keeps_its_name_and_a_global_is_declared():
    """Ghidra's func_0x<offset> names nothing outside the object; the project's own name is what pairs."""
    prelude, definition = drafts.compilable(
        DRAFT.replace("*param_3 = 1;", "*param_3 = _DAT_ffffffff92986c2c;"),
        "FUN_92179300", symbols={"FUN_006C31D0": "FUN_006C31D0"})

    assert "FUN_006C31D0(" in definition and "func_0x006c31d0" not in definition
    assert "extern undefined4 _DAT_ffffffff92986c2c;" in prelude


def test_the_console_offers_ghidra_drafts_not_m2c():
    """Autopilot needs drafts, and m2c has no 64-bit PowerPC target: the console brings Ghidra's instead."""
    assert XBOX360.own_drafts is True
    assert XBOX360.m2c == ""
    assert callable(XBOX360.compilable_draft)


def test_prepare_drafts_writes_the_header_once(tmp_path: Path):
    """The project gets the draft types in include/ghidra.h, and a second call changes nothing."""
    fp = SimpleNamespace(root=tmp_path)
    (tmp_path / "include").mkdir()

    assert XBOX360.prepare_drafts(fp) == "include/ghidra.h"
    assert (tmp_path / "include" / "ghidra.h").read_text(encoding="utf-8") == drafts.GHIDRA_H
    assert XBOX360.prepare_drafts(fp) is None


def test_draft_symbols_lists_the_project_functions(tmp_path: Path):
    """A callee the project knows is found by name, so its call keeps the name objdiff pairs on."""
    (tmp_path / "config" / "game").mkdir(parents=True)
    (tmp_path / "config" / "game" / "functions.json").write_text(
        json.dumps({"units": {"a": [{"name": "FUN_92179300", "addr": 0x92179300}]}}), encoding="utf-8")
    fp = SimpleNamespace(root=tmp_path, manifest={"version": "game"})

    assert XBOX360.draft_symbols(fp) == {"FUN_92179300": "FUN_92179300"}
