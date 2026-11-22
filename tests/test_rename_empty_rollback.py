"""Regression test: rename_symbol must not run setup_commands when nothing was modified."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


def test_rename_symbol_skips_rollback_rebuild_when_nothing_saved(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.c").write_text("void fn_80001000(void) {}\n", encoding="utf-8")
    with net.store.tx() as db:
        db.execute(
            "INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
            ("game", "game", "", "ppc", 1, str(root), json.dumps({"kind": "fork", "platform": "wii", "version": "REXE01"})),
        )
    proj = net.store.one("SELECT * FROM projects WHERE slug='game'")

    setup_called = []
    fp = SimpleNamespace(
        root=root,
        manifest={"version": "REXE01"},
        setup_commands=lambda v: (setup_called.append(v), [["echo", "should-not-run"]])[1],
        run=lambda cmd, log=None: pytest.fail(f"Unexpected rollback command: {cmd}"),
    )
    monkeypatch.setattr(net.forks, "get", lambda slug: (proj, fp, json.loads(proj["config"])))

    with pytest.raises(NetworkError, match="not renamed, nothing changed"):
        net.forks.rename_symbol(None, "game", "fn_80001000", "initGame")

    assert setup_called == []
