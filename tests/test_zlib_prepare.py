"""projects/zlib-n64/prepare.py: zlib comes from the first site whose copy has the pinned checksum (zlib.net sometimes
answers CI's runners with something else), and from none when no copy has it."""

import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "projects" / "zlib-n64" / "prepare.py"


def _load(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("zlib_prepare", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "HERE", tmp_path)
    return mod


def _archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("zlib-1.1.4/zlib.h")
        info.size = 2
        tar.addfile(info, io.BytesIO(b"//"))
    return buf.getvalue()


def test_a_wrong_copy_on_the_first_site_falls_back_to_the_next(tmp_path, monkeypatch):
    good = _archive()
    mod = _load(tmp_path, monkeypatch)
    monkeypatch.setattr(mod.urllib.request, "urlopen",
                        lambda url, timeout: io.BytesIO(b"<html>busy</html>" if url == mod.URLS[0] else good))
    monkeypatch.setattr(mod, "SHA256", hashlib.sha256(good).hexdigest())
    assert (mod.fetch() / "zlib.h").read_bytes() == b"//"


def test_no_copy_with_the_checksum_is_refused_naming_each_site(tmp_path, monkeypatch):
    mod = _load(tmp_path, monkeypatch)
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(b"<html>busy</html>"))
    with pytest.raises(SystemExit, match="checksum mismatch") as e:
        mod.fetch()
    assert all(url in str(e.value) for url in mod.URLS) and not (tmp_path / "original").exists()
