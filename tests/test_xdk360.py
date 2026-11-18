"""The real Microsoft Xbox 360 XDK as a user-supplied kit: a compiler-only synthetic copy (no wine, no real
Microsoft files) still locates, reports its version and exposes cl.exe with runner "wine". No compiler is run.
"""

from pathlib import Path

from decompnet import kits, toolchains
from decompnet.consoles.xbox360 import XBOX360
from decompnet.kits import xdk360
from decompnet.kits.xdk360 import XDK360


def make_xdk(top: Path, build: str = "20353") -> Path:
    """An XDK-shaped copy: top/<build>/XDK/bin/win32/cl.exe and c1.dll, like the user's extractions."""
    root = top / build / "XDK"
    bin_ = root / "bin" / "win32"
    bin_.mkdir(parents=True)
    (bin_ / "cl.exe").write_bytes(b"cl stub")
    (bin_ / "c1.dll").write_bytes(b"c1 stub")
    return root


def test_xdk360_is_a_local_kit_the_console_takes():
    assert XDK360 is kits.get("xdk360") and any(k.id == "xdk360" for k in kits.KITS)
    assert "xdk360" in XBOX360.kits
    tc = toolchains.get("xdk360")
    assert tc.local and tc.supported and not tc.parts
    assert tc.local_files == ["bin/win32/cl.exe"]


def test_locate_accepts_both_the_parent_and_the_XDK_folder(tmp_path: Path):
    root = make_xdk(tmp_path)
    assert XDK360.locate(root) == root.resolve()
    assert XDK360.locate(root.parent) == root.resolve()
    assert XDK360.locate(tmp_path / "nothing") is None


def test_build_prefers_cl_exe_and_exposes_a_wine_compiler(tmp_path: Path, monkeypatch):
    root = make_xdk(tmp_path)
    # c1.dll reports a lower base version; cl.exe's 16.00.x is the one a game's banner prints.
    monkeypatch.setattr(xdk360, "file_version", lambda at: "16.0.11886.0" if at.name.lower() == "cl.exe" else "10.0.1.0")
    assert XDK360.build(root) == "16.0.11886.0"
    assert XDK360.build(root.parent) == "16.0.11886.0"
    [own] = XDK360.compilers(root)
    assert own.version == "xdk360-16.0.11886.0" and own.build == "16.0.11886.0"
    assert own.name == "XDK 16.0.11886.0 PowerPC C/C++"
    assert own.exe == root / "bin" / "win32" / "cl.exe"
    assert own.runner == "wine" and own.root == root.resolve()
    assert own.args == () and own.flags == () and own.includes == ()


def test_folder_build_used_when_no_version_resource(tmp_path: Path, monkeypatch):
    root = make_xdk(tmp_path, "21256.3")
    monkeypatch.setattr(xdk360, "file_version", lambda at: "")
    assert XDK360.build(root) == "21256.3"
    assert XDK360.build(root.parent) == "21256.3"
    [own] = XDK360.compilers(root.parent)  # pointing at the parent works too
    assert own.version == "xdk360-21256.3" and own.exe == root / "bin" / "win32" / "cl.exe"


def test_headers_are_exposed_when_a_full_copy_has_them(tmp_path: Path, monkeypatch):
    root = make_xdk(tmp_path)
    (root / "include" / "xbox").mkdir(parents=True)
    monkeypatch.setattr(xdk360, "file_version", lambda at: "")
    [own] = XDK360.compilers(root)
    assert own.includes == (root / "include" / "xbox",)


def test_a_copy_without_cl_exe_gives_no_compiler(tmp_path: Path):
    assert XDK360.build(tmp_path / "nothing") is None
    assert XDK360.compilers(tmp_path / "nothing") == []
