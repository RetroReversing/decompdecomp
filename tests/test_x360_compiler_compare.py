"""Xbox 360 can pick WHICH registered XDK copy builds a project, like N64's compiler tests but through the project
build: configure.py resolves one copy by version, the console records that choice in the manifest, and
forkengine.try_compiler can switch among copies without the "a fork builds with its project's own compilers" guard.

All kits here are synthetic (fake cl.exe files, no Wine, no real XDK): what is verified is the selection and
regeneration wiring, NOT that any copy reproduces a real game's bytes.
"""

import importlib.util
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import kits
from decompnet.app import forkengine
from decompnet.consoles.xbox360 import XBOX360
from decompnet.consoles.xbox360 import scratch as xscratch
from decompnet.kits import OwnCompiler

CONFIGURE = xscratch.TEMPLATE / "tools" / "configure.py"


def load_configure():
    """Load the template's configure.py without running its CLI (its side effects live in main())."""
    spec = importlib.util.spec_from_file_location("x360_compare_configure", CONFIGURE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_own(version: str, root: Path) -> OwnCompiler:
    """A fake registered xdk360 copy whose cl.exe is a real (empty) file, so exe.is_file() passes."""
    exe = root / version / "cl.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("not a real compiler")
    return OwnCompiler(version=version, name=f"XDK {version}", exe=exe, runner="wine", build=version,
                       includes=(), root=None, args=(), flags=())


@pytest.fixture
def two_xdks(tmp_path, monkeypatch):
    for key in ("XSDK_CC", "DECOMP_X360_COMPILER", "DECOMP_X360_RUNNER"):
        monkeypatch.delenv(key, raising=False)
    a = make_own("xdk360-20353", tmp_path)
    b = make_own("xdk360-21256.3", tmp_path)
    monkeypatch.setattr(kits, "compilers", lambda kids: [a, b])
    return a, b


class FakeFP:
    """Only the ForkProject surface the console hooks and try_compiler read."""

    def __init__(self, root: Path):
        self.root = root

    @property
    def manifest(self):
        p = self.root / ".decomp.wtf.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def project(tmp_path: Path, version: str = "v") -> tuple[Path, FakeFP]:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    (root / ".decomp.wtf.json").write_text(json.dumps({
        "created": "from scratch", "platform": "xbox360", "version": version,
        "compiler": "xbox360-xsdk-placeholder",
    }), encoding="utf-8")
    return root, FakeFP(root)


def test_configure_resolves_exact_copy_and_bakes_it(tmp_path, monkeypatch, two_xdks):
    """(a) DECOMP_X360_COMPILER=<version> picks that exact registered copy (not the first), runner wine, and the
    generated build.ninja's cc rule really carries its cl.exe and runner."""
    a, b = two_xdks
    mod = load_configure()
    monkeypatch.setattr(mod, "MANIFEST", tmp_path / "no-manifest.json")  # isolate from any repo manifest
    monkeypatch.setenv("DECOMP_X360_COMPILER", b.version)
    assert mod.find_compiler() == (str(b.exe), "wine")
    assert mod.find_compiler() != (str(a.exe), "wine")

    root, _ = project(tmp_path)
    (root / "config" / "v").mkdir(parents=True)
    (root / "config" / "v" / "functions.json").write_text(
        json.dumps({"units": {"a": []}}), encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", root)
    monkeypatch.setattr(mod, "MANIFEST", root / ".decomp.wtf.json")
    monkeypatch.setattr(sys, "argv", ["configure.py", "v"])
    mod.main()
    ninja = (root / "build.ninja").read_text(encoding="utf-8")
    assert str(b.exe) in ninja and "--runner wine" in ninja
    assert str(a.exe) not in ninja


def test_a_wine_compiler_gets_msvc_flags_not_gcc(tmp_path, monkeypatch, two_xdks):
    """A real XDK cl.exe rejects GCC flags, so the generated build must default to MSVC switches for runner wine."""
    a, b = two_xdks
    mod = load_configure()
    root, _ = project(tmp_path)
    (root / "config" / "v").mkdir(parents=True)
    (root / "config" / "v" / "functions.json").write_text(
        json.dumps({"units": {"a": []}}), encoding="utf-8")
    monkeypatch.delenv("XSDK_FLAGS", raising=False)
    monkeypatch.setattr(mod, "ROOT", root)
    monkeypatch.setattr(mod, "MANIFEST", root / ".decomp.wtf.json")
    monkeypatch.setattr(sys, "argv", ["configure.py", "v", "--compiler", b.version])
    mod.main()
    ninja = (root / "build.ninja").read_text(encoding="utf-8")
    assert "-- /O2" in ninja and "-mcpu=cell" not in ninja


def test_per_file_compilers_get_their_own_rule(tmp_path, monkeypatch, two_xdks):
    """A file the manifest's compiler map names builds with its own rule; every other unit keeps the default's."""
    a, b = two_xdks
    mod = load_configure()
    root, _ = project(tmp_path)
    (root / "config" / "v").mkdir(parents=True)
    (root / "config" / "v" / "functions.json").write_text(
        json.dumps({"units": {"one": [], "two": []}}), encoding="utf-8")
    (root / ".decomp.wtf.json").write_text(json.dumps({
        "xdk360_compiler": a.version, "xdk360_compiler_map": {"src/auto/two.c": b.version}}), encoding="utf-8")
    monkeypatch.delenv("XSDK_FLAGS", raising=False)
    monkeypatch.setattr(mod, "ROOT", root)
    monkeypatch.setattr(mod, "MANIFEST", root / ".decomp.wtf.json")
    monkeypatch.setattr(sys, "argv", ["configure.py", "v"])

    mod.main()

    ninja = (root / "build.ninja").read_text(encoding="utf-8")
    assert "rule cc\n" in ninja and "rule cc1\n" in ninja  # one rule per distinct compiler
    assert "build build/v/src/auto/one.o: cc src/auto/one.c" in ninja
    assert "build build/v/src/auto/two.o: cc1 src/auto/two.c" in ninja
    assert str(a.exe) in ninja and str(b.exe) in ninja


def test_xsdk_cc_exe_defaults_to_wine(tmp_path, monkeypatch, two_xdks):
    """(b) A cl.exe given through XSDK_CC is run under wine unless DECOMP_X360_RUNNER is explicitly provided."""
    mod = load_configure()
    monkeypatch.setattr(mod, "MANIFEST", tmp_path / "no-manifest.json")
    monkeypatch.setenv("XSDK_CC", "/opt/xdk/bin/cl.exe")
    assert mod.find_compiler() == ("/opt/xdk/bin/cl.exe", "wine")
    monkeypatch.setenv("DECOMP_X360_RUNNER", "")
    assert mod.find_compiler() == ("/opt/xdk/bin/cl.exe", "")
    monkeypatch.setenv("DECOMP_X360_RUNNER", "wibo")
    assert mod.find_compiler() == ("/opt/xdk/bin/cl.exe", "wibo")


def test_file_compiler_roundtrip_and_regeneration(tmp_path, monkeypatch, two_xdks):
    """(c) file_compiler/set_file_compiler round-trip one file's own copy in the manifest's compiler map and
    regenerate build.ninja; naming the project's default again drops the entry."""
    a, b = two_xdks
    root, fp = project(tmp_path)
    recorded = json.loads((root / ".decomp.wtf.json").read_text(encoding="utf-8"))
    recorded["xdk360_compiler"] = a.version  # a project that recorded its default (as every import does)
    (root / ".decomp.wtf.json").write_text(json.dumps(recorded), encoding="utf-8")
    assert XBOX360.file_compiler(fp, "src/a.c") == a.version

    calls = []

    def fake_configure(r: Path, version: str | None, compiler: str | None = None) -> None:
        calls.append((r, version))
        manifest = json.loads((r / ".decomp.wtf.json").read_text(encoding="utf-8"))
        selected = (manifest.get("xdk360_compiler_map") or {}).get("src/a.c") or manifest.get("xdk360_compiler", "default")
        (r / "build.ninja").write_text(f"rule cc\n  command = ... --compiler {selected} --runner wine ...\n",
                                       encoding="utf-8")

    monkeypatch.setattr(xscratch, "_configure", fake_configure)
    assert XBOX360.set_file_compiler(fp, "src/a.c", b.version) == ".decomp.wtf.json"
    assert fp.manifest["xdk360_compiler_map"] == {"src/a.c": b.version}  # the override, not the default
    assert XBOX360.file_compiler(fp, "src/a.c") == b.version
    assert b.version in (root / "build.ninja").read_text(encoding="utf-8")
    assert calls == [(root, "v")]

    XBOX360.set_file_compiler(fp, "src/a.c", a.version)  # try_compiler's finally restores the original
    assert not fp.manifest.get("xdk360_compiler_map")  # the default needs no entry
    assert XBOX360.file_compiler(fp, "src/a.c") == a.version
    assert a.version in (root / "build.ninja").read_text(encoding="utf-8")


def test_try_compiler_tries_both_copies(tmp_path, monkeypatch, two_xdks):
    """(d) forkengine.try_compiler no longer rejects X360 and walks every registered copy, switching the project's
    compiler between attempts and restoring the original at the end (_attempt mocked: no Wine, no real build)."""
    a, b = two_xdks
    root, fp = project(tmp_path)
    (root / "a.c").write_text("int f(void){return 0;}", encoding="utf-8")
    fp.unit = lambda u: SimpleNamespace(source="a.c", name="a")
    fp.source_is_linked = lambda unit: False
    monkeypatch.setattr(xscratch, "_configure", lambda r, v, compiler=None: None)

    seen = []

    def attempt(agent, slug, function, source, **kwargs):
        seen.append(XBOX360.file_compiler(fp, "a.c"))
        return {"matched": False, "text": "score 9", "broken": []}

    class Tree:
        def exclusive(self):
            return nullcontext()

        def shared(self):
            return nullcontext()

    fake = SimpleNamespace(
        get=lambda slug: ({"id": "p1"}, fp, {"platform": "xbox360"}),
        _fn=lambda slug, function: {"unit": "a", "status": "draft", "name": "f"},
        _hold_for_try=lambda *args: None,
        _tree=lambda proj: Tree(),
        _unit_lock=lambda proj, name: nullcontext(),
        _baseline=lambda *args: None,
        _attempt=attempt,
        _commit=lambda *args: None,
        _build_unit=lambda *args: None,
        _build_locks={"p1": nullcontext()},
        net=SimpleNamespace(links={}, _source_of=lambda fn: "int f(void){return 0;}",
                            bump_generation=lambda pid: None,
                            filecompilers=SimpleNamespace(tried=lambda *args: None)),
    )
    result = forkengine.ForkEngine.try_compiler(
        fake, SimpleNamespace(id="ag", name="Agent"), "slug", "f", "all", source="int f(void){return 0;}")
    assert a.version in result and b.version in result  # both copies were tried
    assert b.version in seen  # the project really switched to the second copy for its attempt
    assert XBOX360.file_compiler(fp, "a.c") == a.version  # and the original file compiler was restored
    assert not fp.manifest.get("xdk360_compiler_map")  # nothing left over: the default needs no entry
