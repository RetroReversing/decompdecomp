"""Only app-verified source-less units get probed C candidates, with rollback on unsupported builds."""

import json

import pytest

from decompnet.forks.project import ForkError, ForkProject
from decompnet.forks.scaffold import prepare


def project(root):
    ref = root / '.decomp-wtf/objdiff-references'
    (ref / 'objects').mkdir(parents=True)
    (ref / 'asm').mkdir()
    (ref / 'objects/demo.o').write_bytes(b'verified reference')
    (ref / 'asm/demo.s').write_text('glabel demo\njr $ra\nnop\n')
    cfg = {'units': [{'name': 'asm/demo', 'target_path': '.decomp-wtf/objdiff-references/objects/demo.o'}]}
    (root / 'objdiff.json').write_text(json.dumps(cfg))
    return ForkProject(root)


def test_scaffold_probes_real_object_rule_and_preserves_reference(tmp_path, monkeypatch):
    fp = project(tmp_path)
    def build(unit):
        assert unit.source == 'src/decompdecomp/demo.c'
        unit.base.parent.mkdir(parents=True)
        unit.base.write_bytes(b'compiled empty candidate')
        return True, 'ok'
    monkeypatch.setattr(fp, 'build_unit', build)
    assert prepare(fp) == ['src/decompdecomp/demo.c']
    assert prepare(fp) == []
    entry = json.loads((tmp_path / 'objdiff.json').read_text())['units'][0]
    assert entry['base_path'] == 'build/src/decompdecomp/demo.c.o'
    assert entry['metadata']['draft_assembly'] == '.decomp-wtf/objdiff-references/asm/demo.s'
    unit = fp.units()[0]
    assert unit.target.read_bytes() == b'verified reference'
    assert unit.base.read_bytes() == b'compiled empty candidate'
    assert 'M2C_DEFINES' in (tmp_path / unit.source).read_text()


@pytest.mark.parametrize('failure', ['failed', 'missing_object'])
def test_failed_probe_rolls_back_sources_and_config(tmp_path, monkeypatch, failure):
    fp = project(tmp_path)
    original = (tmp_path / 'objdiff.json').read_bytes()
    monkeypatch.setattr(fp, 'build_unit', lambda unit: (failure != 'failed', 'no supported rule'))
    with pytest.raises(ForkError, match='cannot compile'):
        prepare(fp)
    assert (tmp_path / 'objdiff.json').read_bytes() == original
    assert not (tmp_path / 'src/decompdecomp/demo.c').exists()


def test_unrelated_target_and_existing_user_file_are_retained(tmp_path):
    fp = project(tmp_path)
    source = tmp_path / 'src/decompdecomp/demo.c'
    source.parent.mkdir(parents=True)
    source.write_text('user code')
    with pytest.raises(ForkError, match='already exists'):
        prepare(fp)
    assert source.read_text() == 'user code'
    (tmp_path / 'objdiff.json').write_text(json.dumps({'units': [{'name': 'other', 'target_path': 'expected/other.o'}]}))
    assert prepare(fp) == []


def test_imported_project_can_prepare_autopilot_without_origin_gate(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from decompnet.app.forkengine import ForkEngine
    from decompnet.forks import scaffold

    fp = project(tmp_path)
    monkeypatch.setattr(scaffold, 'prepare', lambda project: [])
    monkeypatch.setattr('decompnet.app.forkengine.toolchains.installed', lambda tool: {'id': tool})
    store = SimpleNamespace(all=lambda *args: [{'id': 17}])
    fake = SimpleNamespace(get=lambda slug: ({'id': 1}, fp, {'origin': 'local', 'platform': 'n64', 'ready': True}),
                           _tree=lambda proj: SimpleNamespace(exclusive=nullcontext),
                           _build_locks={1: nullcontext()}, net=SimpleNamespace(store=store))
    assert ForkEngine.autopilot_prepare(fake, 'demo') == [17]
