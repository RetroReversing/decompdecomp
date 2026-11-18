"""Xbox 360 build tools: refreshing an imported project's stale tools and the Wine build env, no real Wine."""

import json
import struct
from pathlib import Path

from decompnet.consoles.xbox360 import XBOX360, scratch


def test_write_tools_refreshes_stale_tools(tmp_path: Path):
    """An imported project's tools are brought up to the current template when their content differs."""
    root = tmp_path / "project"
    (root / "tools").mkdir(parents=True)
    assert scratch.TOOLS  # the template really has tool files to refresh
    for rel in scratch.TOOLS:
        (root / rel).write_text("# stale configure.py from an older decompdecomp\n", encoding="utf-8")

    changed = scratch.write_tools(root)

    assert set(changed) == set(scratch.TOOLS)
    for rel in scratch.TOOLS:
        assert (root / rel).read_text(encoding="utf-8") == (scratch.TEMPLATE / rel).read_text(encoding="utf-8")


def test_write_tools_creates_missing_and_is_idempotent(tmp_path: Path):
    """A tool the project never had is written too, and a second refresh changes nothing."""
    root = tmp_path / "project"
    (root / "tools").mkdir(parents=True)
    first = scratch.TOOLS[0]
    (root / first).write_text("stale\n", encoding="utf-8")  # the others are missing entirely

    changed = scratch.write_tools(root)

    assert set(changed) == set(scratch.TOOLS)
    assert scratch.write_tools(root) == []  # idempotent: no needless rewrite on every start


def test_tools_version_tracks_template_content(tmp_path: Path, monkeypatch):
    """The manifest's stamp changes when a template tool changes, so on_start only refreshes when needed."""
    fake_template = tmp_path / "project_template"
    (fake_template / "tools").mkdir(parents=True)
    for rel in scratch.TOOLS:
        (fake_template / rel).write_text("version one\n", encoding="utf-8")
    monkeypatch.setattr(scratch, "TEMPLATE", fake_template)

    before = scratch.tools_version()
    assert scratch.tools_version() == before  # stable across calls
    (fake_template / scratch.TOOLS[0]).write_text("version two\n", encoding="utf-8")
    assert scratch.tools_version() != before


class _Fork:
    def __init__(self, root: Path, manifest: dict):
        self.root = root
        self.manifest = manifest

    def update_manifest(self, **keys):
        self.manifest.update(keys)


class _Net:
    def __init__(self, fp: _Fork, slug: str):
        self._fp = fp
        self._slug = slug

        class Forks:
            def get(_self, slug):
                return (None, fp)

        self.forks = Forks()

    def fork_slug(self, slug: str) -> bool:
        return slug == self._slug


def test_on_start_refreshes_tools_and_stamps_manifest(tmp_path: Path):
    """Starting an existing imported project updates its stale tools once and records the version."""
    root = tmp_path / "project"
    (root / "tools").mkdir(parents=True)
    for rel in scratch.TOOLS:
        (root / rel).write_text("stale\n", encoding="utf-8")
    fp = _Fork(root, {"created": "from scratch", "platform": "xbox360"})
    net = _Net(fp, "my-game")
    said = []
    version = scratch.tools_version()

    XBOX360.on_start(net, "my-game", log=said.append)

    assert fp.manifest["tools"] == version
    assert any("updated this project's build tools" in line for line in said)
    for rel in scratch.TOOLS:
        assert (root / rel).read_text(encoding="utf-8") == (scratch.TEMPLATE / rel).read_text(encoding="utf-8")
    # a second start with the stamp recorded does nothing
    said.clear()
    XBOX360.on_start(net, "my-game", log=said.append)
    assert said == []


def test_on_start_ignores_foreign_and_unknown_projects(tmp_path: Path):
    """Public forks and unknown slugs are left alone (only 'from scratch' projects have these tools)."""
    root = tmp_path / "project"
    (root / "tools").mkdir(parents=True)
    (root / scratch.TOOLS[0]).write_text("stale\n", encoding="utf-8")
    fp = _Fork(root, {"created": "from repository"})
    XBOX360.on_start(_Net(fp, "my-game"), "other-game")  # unknown slug
    XBOX360.on_start(_Net(fp, "my-game"), "my-game")  # not created from scratch
    assert (root / scratch.TOOLS[0]).read_text(encoding="utf-8") == "stale\n"


def test_build_env_uses_wine(monkeypatch):
    """A real XDK build gets the app's Wine and prefix (with always=True), without starting real Wine."""
    from decompnet import wine

    seen = {}

    def fake_build_env(always: bool = False) -> dict[str, str]:
        seen["always"] = always
        return {"DECOMPNET_WINE": "/opt/wine/bin/wine", "DECOMPNET_WINE_PREFIX": "/tmp/prefix"}

    monkeypatch.setattr(wine, "build_env", fake_build_env)

    env = XBOX360.build_env()

    assert seen["always"] is True
    assert env["DECOMPNET_WINE"] == "/opt/wine/bin/wine"
    assert env["DECOMPNET_WINE_PREFIX"] == "/tmp/prefix"


def _pe(stamp: int) -> bytes:
    """The bytes ``pe_timestamp`` reads: an MZ header naming a PE at 0x80, with ``stamp`` in its COFF header."""
    image = bytearray(0x200)
    image[0:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHI", image, 0x84, 0x1F2, 3, stamp)  # PowerPC, 3 sections, the link stamp
    return bytes(image)


def test_pe_timestamp_dates_a_compiler_and_a_game(tmp_path: Path):
    """The survey's date evidence: a XDK's cl.exe is a PE, and a game's XEX2 wraps one."""
    plain = tmp_path / "cl.exe"
    plain.write_bytes(_pe(1353000000))
    assert scratch.pe_timestamp(plain) == 1353000000.0

    game = tmp_path / "game.xex"
    game.write_bytes(b"XEX2" + bytes(4) + struct.pack(">I", 0x100) + bytes(0xF4) + _pe(1353000000))
    assert scratch.pe_timestamp(game) == 1353000000.0


def test_pe_timestamp_is_none_for_what_it_cannot_read(tmp_path: Path):
    """A missing file, a file that isn't a PE, a zero stamp and a truncated XEX2 are all "no date", not an error."""
    assert scratch.pe_timestamp(tmp_path / "missing.exe") is None
    (tmp_path / "junk").write_bytes(b"not a PE at all")
    assert scratch.pe_timestamp(tmp_path / "junk") is None
    zero = tmp_path / "zero.exe"
    zero.write_bytes(_pe(0))
    assert scratch.pe_timestamp(zero) is None
    lying = tmp_path / "lying.xex"
    lying.write_bytes(b"XEX2" + bytes(4) + struct.pack(">I", 0x100))  # names a PE that isn't there
    assert scratch.pe_timestamp(lying) is None


def test_a_trial_switch_does_not_record_a_choice(tmp_path: Path, monkeypatch):
    """The survey switches build.ninja to a copy it is only testing; the manifest keeps the project's own copy
    until one is chosen, so a cancelled survey can never record a copy it never settled on."""
    root = tmp_path / "project"
    root.mkdir()
    (root / ".decomp.wtf.json").write_text(json.dumps({"xdk360_compiler": "xdk360-a"}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(scratch, "_configure", lambda root, version, compiler=None: calls.append(compiler))

    scratch.set_xdk360_compiler(root, "v", "xdk360-b", record=False)

    assert json.loads((root / ".decomp.wtf.json").read_text(encoding="utf-8"))["xdk360_compiler"] == "xdk360-a"
    assert calls == ["xdk360-b"]  # build.ninja got the trial copy explicitly

    scratch.set_xdk360_compiler(root, "v", "xdk360-b")

    assert json.loads((root / ".decomp.wtf.json").read_text(encoding="utf-8"))["xdk360_compiler"] == "xdk360-b"
    assert calls == ["xdk360-b", None]  # a recorded choice is read from the manifest


def test_game_build_time_follows_the_manifest(tmp_path: Path):
    """A project's manifest names its game; the survey dates the title from that copy when it's still on disk."""
    (tmp_path / "orig").mkdir()
    (tmp_path / "orig" / "game.xex").write_bytes(
        b"XEX2" + bytes(4) + struct.pack(">I", 0x100) + bytes(0xF4) + _pe(1353000000))
    assert scratch.game_build_time(tmp_path, {"game": {"path": "orig/game.xex"}}) == 1353000000.0
    assert scratch.game_build_time(tmp_path, {"game": {"path": "orig/gone.xex"}}) is None
    assert scratch.game_build_time(tmp_path, {}) is None
