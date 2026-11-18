"""Stage 2a: the xsdk kit is a user copy (no pinned toolchain), and its compiler shows up
on the Xbox 360 console once Setup records that copy. No real toolchain is needed: the
stand-in compiler is a shell script that answers -dumpversion with 15.3.0.
"""

from pathlib import Path

from decompnet import kits, toolchains
from decompnet.consoles.xbox360 import XBOX360
from decompnet.kits.xsdk import XSDK

ROOT = Path(__file__).resolve().parent.parent
STUB = b'#!/bin/sh\nif [ "$1" = "-dumpversion" ]; then echo 15.3.0; exit 0; fi\nexit 0\n'


def make_xsdk(top: Path) -> Path:
    """A folder holding the bin/tools/src layout the xsdk README documents."""
    root = top / "xsdk"
    compiler = root / "bin" / "powerpc-xsdk-elf-gcc"
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(STUB)
    compiler.chmod(0o755)
    (root / "tools").mkdir()
    (root / "tools" / "elf2xex.py").write_text("# XEX2 packer stand-in\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "xsdk.ld").write_text("ENTRY(xsdk_start)\n", encoding="utf-8")
    return root


def make_prefix(top: Path) -> Path:
    """A compiler prefix on its own: the SDK root is a different folder."""
    compiler = top / "xsdk-gcc" / "bin" / "powerpc-xsdk-elf-gcc"
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(STUB)
    compiler.chmod(0o755)
    return compiler.parent.parent


def _setup(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path / "toolchains"))


def test_xsdk_is_a_local_kit_the_console_takes():
    assert XSDK is kits.get("xsdk") and any(k.id == "xsdk" for k in kits.KITS)
    assert "xsdk" in XBOX360.kits
    tc = toolchains.get("xsdk")
    assert tc.local and tc.supported and not tc.parts
    assert tc.local_files == ["bin/powerpc-xsdk-elf-gcc"]


def test_the_kit_reads_its_version_and_gives_one_native_compiler(tmp_path: Path):
    kit, copy = kits.get("xsdk"), make_xsdk(tmp_path)
    assert kit.locate(copy) == copy.resolve() and kit.locate(tmp_path / "nothing") is None
    assert kit.build(copy) == "15.3.0"
    [own] = kit.compilers(copy.resolve())
    assert own.version == "xsdk-15.3.0" and own.build == "15.3.0"
    assert own.name == "xsdk GCC 15.3.0 (your copy)"
    assert own.exe == copy / "bin" / "powerpc-xsdk-elf-gcc"
    assert own.runner == "" and own.args == ("-c",) and own.includes == ()
    assert own.flags == ("-O2", "-mcpu=cell", "-fno-pic", "-mcall-xsdk")
    assert own.root == copy.resolve() and "15.3.0" in kit.verify(copy)


def test_a_separate_compiler_prefix_uses_XSDK_ROOT(tmp_path: Path, monkeypatch):
    kit, prefix, sdk = kits.get("xsdk"), make_prefix(tmp_path / "host"), make_xsdk(tmp_path / "repo")
    monkeypatch.delenv("XSDK_ROOT", raising=False)
    assert kit.locate(prefix) is None  # no SDK files in the same tree and no env var
    monkeypatch.setenv("XSDK_ROOT", str(sdk))
    assert kit.locate(prefix) == prefix.resolve() and kit.build(prefix) == "15.3.0"
    [own] = kit.compilers(prefix.resolve())
    assert own.version == "xsdk-15.3.0" and own.exe == prefix / "bin" / "powerpc-xsdk-elf-gcc"
    # The other direction works too: the SDK root is given, the compiler is named by XSDK_GCC.
    monkeypatch.setenv("XSDK_GCC", str(prefix / "bin" / "powerpc-xsdk-elf-gcc"))
    assert kit.locate(sdk) == sdk.resolve() and kit.build(sdk) == "15.3.0"


def test_the_console_lists_the_kits_compiler_when_the_copy_is_set_up(tmp_path: Path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    copy = make_xsdk(tmp_path)
    toolchains.install("xsdk", folder=copy, accept_license=True, log=lambda *_: None)
    assert kits.folder("xsdk") == copy.resolve()
    assert [c.id for c in XBOX360.kit_compilers()] == ["xsdk-15.3.0"]
    ids = [c.id for c in XBOX360.compilers]
    assert "xbox360-xsdk-placeholder" in ids and "xsdk-15.3.0" in ids
    assert any(c["id"] == "xsdk-15.3.0" for c in XBOX360.describe()["compilers"])


def test_the_demo_is_declared_and_original():
    assert XBOX360.demo == "demo-xbox360" and XBOX360.demo_tools == ("ghidra", "objdiff-cli")
    folder = ROOT / "projects" / XBOX360.demo
    assert (folder / "build.py").is_file() and (folder / "demo.c").is_file()
