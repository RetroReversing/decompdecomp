"""The xsdk kit with its compiler prefix and SDK checkout in separate trees: pointing Setup at either half works.

No real toolchain: the "compiler" is a shell script answering -dumpversion with 15.3.0, and the SDK is just its two
marker files. Path.home() is redirected into tmp so a real ~/xsdk-gcc or ~/Projects/xsdk cannot leak into a test.
"""

from pathlib import Path

import pytest

from decompnet import kits

GCC = "powerpc-xsdk-elf-gcc"
STUB = b'#!/bin/sh\nif [ "$1" = "-dumpversion" ]; then echo 15.3.0; exit 0; fi\nexit 0\n'
FLAGS = ("-O2", "-mcpu=cell", "-fno-pic", "-mcall-xsdk")


def make_prefix(top: Path) -> Path:
    """A compiler prefix on its own: bin/<gcc>, with no SDK files anywhere in it."""
    compiler = top / "xsdk-gcc" / "bin" / GCC
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(STUB)
    compiler.chmod(0o755)
    return compiler.parent.parent


def make_sdk(top: Path) -> Path:
    """An SDK checkout on its own: the packer and link script, with no compiler inside it."""
    root = top / "xsdk"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "elf2xex.py").write_text("# XEX2 packer stand-in\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "xsdk.ld").write_text("ENTRY(xsdk_start)\n", encoding="utf-8")
    return root


def isolated(monkeypatch, home: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("XSDK_ROOT", raising=False)
    monkeypatch.delenv("XSDK_GCC", raising=False)


def test_both_halves_of_the_users_split_layout_are_accepted(tmp_path: Path, monkeypatch):
    """The compiler prefix ~/xsdk-gcc and the checkout ~/Projects/xsdk are two trees; either one registers."""
    kit = kits.get("xsdk")
    home = tmp_path / "home"
    isolated(monkeypatch, home)
    prefix, sdk = make_prefix(home), make_sdk(home / "Projects")

    assert kit.locate(prefix) == prefix.resolve()
    assert kit.locate(sdk) == sdk.resolve()
    assert kit.sdk_root(prefix) == sdk and kit.sdk_root(sdk) == sdk
    assert kit.build(prefix) == "15.3.0" and kit.build(sdk) == "15.3.0"
    assert "15.3.0" in kit.verify(prefix) and "15.3.0" in kit.verify(sdk)

    [from_prefix] = kit.compilers(prefix.resolve())
    assert from_prefix.exe == prefix / "bin" / GCC and from_prefix.root == prefix.resolve()
    assert from_prefix.flags == FLAGS and from_prefix.build == "15.3.0"
    # From the SDK half, the compiler is the common ~/xsdk-gcc prefix; root still mounts that prefix in the sandbox.
    [from_sdk] = kit.compilers(sdk.resolve())
    assert from_sdk.exe == home / "xsdk-gcc" / "bin" / GCC
    assert from_sdk.root == prefix.resolve()


@pytest.mark.parametrize("where", ["Projects", "", "xsdk-gcc"])
def test_the_sdk_is_found_in_common_nearby_locations(tmp_path: Path, monkeypatch, where: str):
    """`where` is the parent of the checkout: prefix.parent/Projects, prefix.parent, and prefix itself."""
    kit = kits.get("xsdk")
    isolated(monkeypatch, tmp_path / "empty-home")
    prefix = make_prefix(tmp_path / "host")
    sdk = make_sdk(tmp_path / "host" / where)

    assert kit.locate(prefix) == prefix.resolve()
    assert kit.sdk_root(prefix) == sdk
    assert kit.build(prefix) == "15.3.0"
    [own] = kit.compilers(prefix.resolve())
    assert own.exe == prefix / "bin" / GCC


def test_a_compiler_with_no_sdk_anywhere_is_rejected(tmp_path: Path, monkeypatch):
    kit = kits.get("xsdk")
    isolated(monkeypatch, tmp_path / "empty-home")
    prefix = make_prefix(tmp_path / "host")

    assert kit.sdk_root(prefix) is None
    assert kit.locate(prefix) is None  # Setup refuses the copy: no SDK to pack an ELF into XEX2
    [own] = kit.compilers(prefix)  # the driver itself still reads; it just isn't registered without an SDK
    assert own.exe == prefix / "bin" / GCC
    assert kit.build(prefix) == "15.3.0"


def test_configure_uses_the_registered_kit_compiler_without_env_vars(tmp_path: Path, monkeypatch):
    """The generated build names the kit's compiler; the user doesn't have to set XSDK_CC by hand."""
    import os
    import subprocess
    import sys

    from decompnet import toolchains

    isolated(monkeypatch, tmp_path / "empty-home")
    prefix = make_prefix(tmp_path / "host")
    make_sdk(tmp_path / "host" / "Projects")
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path / "toolchains"))
    monkeypatch.delenv("XSDK_CC", raising=False)
    toolchains.install("xsdk", folder=prefix, accept_license=True, log=lambda *_: None)

    source = (Path(__file__).resolve().parent.parent / "src" / "decompnet" / "consoles" / "xbox360"
              / "project_template" / "tools" / "configure.py")
    project = tmp_path / "project"
    (project / "tools").mkdir(parents=True)
    (project / "tools" / "configure.py").write_bytes(source.read_bytes())
    (project / "config" / "1").mkdir(parents=True)
    (project / "config" / "1" / "functions.json").write_text('{"units": ["main"]}', encoding="utf-8")

    done = subprocess.run([sys.executable, "tools/configure.py", "1"], cwd=project, env=os.environ.copy(),
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert str(prefix / "bin" / GCC) in (project / "build.ninja").read_text(encoding="utf-8")
    assert "warning" not in done.stderr
