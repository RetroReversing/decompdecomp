"""Tests for mtime/size-keyed caching in candidate_scope.digest()."""

from __future__ import annotations

import hashlib

from decompnet import candidate_scope


def test_digest_caches_unchanged_file_and_invalidates_on_change(tmp_path, monkeypatch):
    target = tmp_path / "header.h"
    target.write_text("int x;\n", encoding="utf-8")

    reads = []
    real_read_bytes = type(target).read_bytes

    def counting_read_bytes(self):
        reads.append(self)
        return real_read_bytes(self)

    monkeypatch.setattr(type(target), "read_bytes", counting_read_bytes)

    d1 = candidate_scope.digest(target)
    d2 = candidate_scope.digest(target)
    assert d1 == hashlib.sha256(b"int x;\n").hexdigest()
    assert d2 == d1
    assert len(reads) == 1

    target.write_text("int x; int y;\n", encoding="utf-8")
    d3 = candidate_scope.digest(target)
    assert d3 == hashlib.sha256(b"int x; int y;\n").hexdigest()
    assert d3 != d1
    assert len(reads) == 2


def test_digest_returns_unavailable_for_missing_file(tmp_path):
    assert candidate_scope.digest(tmp_path / "missing.h") == "unavailable"
