"""Already placed game files remain intact, while version checks still run."""

import hashlib
import json
import os

import pytest

from decompnet.forks.project import ForkError, ForkProject


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("alias", ["same", "hardlink", "symlink"])
def test_an_already_placed_game_is_validated_without_copying_or_moving(tmp_path, move, alias):
    data = b"synthetic game input"
    dest = tmp_path / "orig" / "game.bin"
    dest.parent.mkdir()
    dest.write_bytes(data)
    (tmp_path / ".decomp.wtf.json").write_text(json.dumps({
        "game": {"path": "orig/", "sha1": hashlib.sha1(data).hexdigest()}}))
    src = dest
    if alias != "same":
        src = tmp_path / "selected.bin"
        try:
            if alias == "hardlink":
                os.link(dest, src)
            else:
                src.symlink_to(dest)
        except OSError:
            pytest.skip("host does not allow this kind of link")
    project = ForkProject(tmp_path)
    assert project.place_game(src, "game.bin", move=move) == dest
    assert src.exists() and dest.read_bytes() == data
    dest.write_bytes(b"wrong version")
    with pytest.raises(ForkError, match="sha1 doesn't match"):
        project.place_game(src, "game.bin", move=move)


def test_an_already_placed_file_target_is_left_in_place(tmp_path):
    dest = tmp_path / "baserom.bin"
    dest.write_bytes(b"synthetic input")
    (tmp_path / ".decomp.wtf.json").write_text(json.dumps({"game": {"path": "baserom.bin"}}))
    assert ForkProject(tmp_path).place_game(dest, dest.name) == dest
    assert dest.read_bytes() == b"synthetic input"
