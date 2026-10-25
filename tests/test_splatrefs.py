"""Synthetic references must come from the input image, with failed or unsupported imports leaving no config."""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
import yaml
from elftools.elf.elffile import ELFFile

from decompnet import consoles, toolchains
from decompnet.forks import splatrefs
from decompnet.forks.project import ForkError, ForkProject

from .test_ps2_debug import SHF_ALLOC, SHF_EXECINSTR, SHT_PROGBITS, _elf


def config(root):
    (root / 'src').mkdir(exist_ok=True)
    (root / 'build/src').mkdir(parents=True, exist_ok=True)
    (root / 'src/demo.c').write_text('int demo_func(void) { return 8; }')
    (root / 'build/src/demo.o').write_bytes(b'candidate, not a reference')
    return {'options': {'src_path': 'src', 'build_path': 'build', 'platform': 'n64'},
            'segments': [{'type': 'code', 'start': 0, 'vram': 0x80000400,
                          'subsegments': [[0, 'c', 'demo'], {'type': 'bss', 'vram': 0x80000410}]}, [16]]}


def test_plan_keeps_original_rom_addresses_and_candidate_paths(tmp_path):
    result = splatrefs.plan(tmp_path, config(tmp_path))
    assert result == [{'name': 'demo', 'source': tmp_path / 'src/demo.c',
                       'base': tmp_path / 'build/src/demo.o', 'offset': 0, 'vram': 0x80000400,
                       'delta': -0x80000400}]


@pytest.mark.parametrize('change', ['overlay', 'compression', 'unknown', 'duplicate', 'escape', 'missing'])
def test_unsupported_layouts_cannot_publish_partial_configuration(tmp_path, change):
    cfg = config(tmp_path)
    segment = cfg['segments'][0]
    if change == 'overlay':
        segment['exclusive_ram_id'] = 'bank'
    elif change == 'compression':
        segment['compressed'] = True
    elif change == 'unknown':
        segment['subsegments'][0][1] = 'custom_decoder'
    elif change == 'duplicate':
        segment['subsegments'].append([8, 'c', 'demo'])
    elif change == 'escape':
        segment['subsegments'][0][2] = '../../outside'
    else:
        (tmp_path / 'build/src/demo.o').unlink()
    with pytest.raises(ForkError):
        splatrefs.plan(tmp_path, cfg)
    assert not (tmp_path / 'objdiff.json').exists()


def test_verification_rejects_wrong_bytes_addresses_and_unmapped_sections(tmp_path):
    image = b'original image bytes'
    unit = {'delta': -0x80000400}
    path = tmp_path / 'reference.elf'
    for section, address, data in [('.text', 0x80000400, b'changed!'),
                                   ('.text', 0x80000300, image),
                                   ('.text', 0x80000400, image + b'extra'),
                                   ('.other', 0x80000400, image)]:
        path.write_bytes(_elf([(section, SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, address, data, 0)]))
        with pytest.raises(ForkError):
            splatrefs.verify(path, image, unit)
    path.write_bytes(_elf([('.text', SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0x80000400, image, 0)]))
    splatrefs.verify(path, image, unit)


def test_existing_objdiff_is_never_replaced_or_even_read(tmp_path):
    cfg = tmp_path / 'objdiff.json'
    cfg.write_text('user-managed configuration')
    assert not splatrefs.generate(SimpleNamespace(root=tmp_path), tmp_path / 'unused.yaml', {}, None)
    assert cfg.read_text() == 'user-managed configuration'


def test_wrong_rom_is_rejected_before_running_any_tools(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg['sha1'] = '0' * 40
    cfg['options']['target_path'] = 'baserom.z64'
    (tmp_path / 'baserom.z64').write_bytes(b'wrong image')
    monkeypatch.setattr(splatrefs, 'run', lambda *a, **k: pytest.fail('tools must not run'))
    with pytest.raises(ForkError, match='exact ROM'):
        splatrefs.generate(SimpleNamespace(root=tmp_path), tmp_path / 'splat.yaml', cfg, consoles.get('n64'))


def test_setup_calls_the_generator_only_after_a_successful_build(tmp_path, monkeypatch):
    project = ForkProject(tmp_path)
    monkeypatch.setattr(project, 'unpack_disc', lambda *a, **k: None)
    monkeypatch.setattr(project, 'setup_commands', lambda *a: [['make']])
    seen = []
    monkeypatch.setattr(project, 'run', lambda *a, **k: (seen.append('build') or True, 'built'))
    def generate(**kwargs):
        seen.append('generate')
        (tmp_path / 'objdiff.json').write_text('{"units": []}')
    monkeypatch.setattr(project, 'generate_objdiff', generate)
    project.setup(log=lambda *a: None)
    assert seen == ['build', 'generate']
    project.setup(log=lambda *a: None)
    assert seen == ['build', 'generate', 'build']


def synthetic_project(root, platform):
    """Assemble a tiny original program and a deliberately different candidate, without a proprietary compiler."""
    cfg = config(root)
    console = consoles.get(platform)
    cfg['options']['platform'] = platform
    tools = toolchains.require('binutils-mips')
    suffix = '.exe' if (tools / 'mips-ps2-decompals-as.exe').exists() else ''
    assembler, linker = (str(tools / ('mips-ps2-decompals-' + name + suffix)) for name in ['as', 'ld'])
    source = '.set noreorder\n.text\n.globl demo_func\n.type demo_func,@function\ndemo_func:\naddiu $v0,$zero,7\njr $ra\nnop\n.size demo_func,.-demo_func\n'
    (root / 'original.s').write_text(source)
    splatrefs.run([assembler, *console.as_flags, '-o', 'build/original.o', 'original.s'], root, print)
    splatrefs.run([linker, *(flag for flag in console.as_flags if flag in ('-EB', '-EL')), '-Ttext', '0x80000400', '-e', 'demo_func', '-o', 'build/program.elf', 'build/original.o'], root, print)
    with (root / 'build/program.elf').open('rb') as stream:
        image = ELFFile(stream).get_section_by_name('.text').data()
    (root / 'baserom.z64').write_bytes(image)
    (root / 'build/program.z64').write_bytes(image)
    (root / 'candidate.s').write_text(source.replace(',7', ',8'))
    splatrefs.run([assembler, *console.as_flags, '-o', 'build/src/demo.o', 'candidate.s'], root, print)
    (root / 'include').mkdir()
    (root / 'include/macro.inc').write_text('.macro glabel name\n.global \\name\n.type \\name,@function\n\\name:\n.endm\n.macro endlabel name\n.size \\name,.-\\name\n.endm\n.macro nonmatching name,size=1\n.endm\n')
    cfg['segments'][0]['subsegments'] = [[0, 'c', 'demo']]
    cfg['sha1'] = hashlib.sha1(image).hexdigest()
    cfg['options'].update(basename='demo', target_path='baserom.z64', base_path='.', o_as_suffix=True,
                          compiler='IDO' if platform == 'n64' else 'GCC', ld_script_path='demo.ld', symbol_addrs_path=['symbols.txt'])
    (root / 'symbols.txt').write_text('demo_func = 0x80000400; // type:func\n')
    cfg['segments'][-1] = [len(image)]
    (root / 'splat.yaml').write_text(yaml.safe_dump(cfg))
    return cfg


@pytest.mark.skipif(toolchains.installed('binutils-mips') is None, reason='needs pinned MIPS binutils')
@pytest.mark.parametrize('platform', ['n64', 'psx', 'ps2'])
def test_generated_targets_are_rom_derived_and_failed_generation_is_atomic(tmp_path, monkeypatch, platform):
    cfg = synthetic_project(tmp_path, platform)
    source_before = (tmp_path / 'src/demo.c').read_bytes()
    includes_before = {p.relative_to(tmp_path): p.read_bytes() for p in (tmp_path / 'include').rglob('*') if p.is_file()}
    real_verify = splatrefs.verify
    monkeypatch.setattr(splatrefs, 'verify', lambda *a: (_ for _ in ()).throw(ForkError('injected verification failure')))
    with pytest.raises(ForkError, match='injected verification failure'):
        splatrefs.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', copy.deepcopy(cfg), consoles.get(platform))
    assert not (tmp_path / 'objdiff.json').exists()
    assert not (tmp_path / '.decomp-wtf/objdiff-references').exists()
    monkeypatch.setattr(splatrefs, 'verify', real_verify)
    assert splatrefs.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, consoles.get(platform))
    objdiff = json.loads((tmp_path / 'objdiff.json').read_text())
    target = tmp_path / objdiff['units'][0]['target_path']
    base = tmp_path / objdiff['units'][0]['base_path']
    with target.open('rb') as stream:
        expected = ELFFile(stream).get_section_by_name('.text').data()
    with base.open('rb') as stream:
        candidate = ELFFile(stream).get_section_by_name('.text').data()
    assert expected != candidate
    assert expected == (tmp_path / 'baserom.z64').read_bytes()
    assert (tmp_path / 'src/demo.c').read_bytes() == source_before
    assert {p.relative_to(tmp_path): p.read_bytes() for p in (tmp_path / 'include').rglob('*') if p.is_file()} == includes_before
    assert cfg['segments'][0]['subsegments'][0][1] == 'c'
    assert not splatrefs.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, consoles.get(platform))


def test_paths_cannot_escape_the_checkout_through_symlinks(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    root = tmp_path / 'project'
    root.mkdir()
    try:
        (root / 'linked').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('host does not allow directory symlinks')
    with pytest.raises(ForkError, match='inside this checkout'):
        splatrefs.inside(root, 'linked/reference.o')


@pytest.mark.skipif(toolchains.installed('binutils-mips') is None, reason='needs pinned MIPS binutils')
@pytest.mark.parametrize('object_suffix', ['.c.o', '.o'])
def test_verified_full_rebuild_snapshots_linked_overlay_objects(tmp_path, object_suffix):
    cfg = synthetic_project(tmp_path, 'n64')
    object_path = 'build/src/demo' + object_suffix
    tools = toolchains.require('binutils-mips')
    assembler = str(tools / 'mips-ps2-decompals-as')
    linker = str(tools / 'mips-ps2-decompals-ld')
    objcopy = str(tools / 'mips-ps2-decompals-objcopy')
    splatrefs.run([assembler, *consoles.get('n64').as_flags, '-o', object_path, 'original.s'], tmp_path, print)
    splatrefs.run([linker, '-EB', '-Ttext', '0x0', '-Map', 'build/program.map', '-e', 'demo_func',
                   '-o', 'build/program.elf', object_path], tmp_path, print)
    splatrefs.run([objcopy, '-O', 'binary', 'build/program.elf', 'baserom.z64'], tmp_path, print)
    cfg['sha1'] = hashlib.sha1((tmp_path / 'baserom.z64').read_bytes()).hexdigest()
    cfg['segments'].insert(0, {'type': 'code', 'start': 0, 'vram': 0,
                               'subsegments': [[0, 'textbin', 'microcode']]})
    cfg['segments'][1]['exclusive_ram_id'] = 'overlay'
    assert splatrefs.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, consoles.get('n64'))
    objdiff = json.loads((tmp_path / 'objdiff.json').read_text())
    assert len(objdiff['units']) == 1
    unit = objdiff['units'][0]
    assert (tmp_path / unit['target_path']).read_bytes() == (tmp_path / unit['base_path']).read_bytes()


@pytest.mark.skipif(toolchains.installed('binutils-mips') is None, reason='needs pinned MIPS binutils')
def test_overlay_snapshot_rejects_unverified_elf(tmp_path):
    cfg = synthetic_project(tmp_path, 'n64')
    cfg['segments'][0]['exclusive_ram_id'] = 'overlay'
    altered = (tmp_path / 'baserom.z64').read_bytes() + b'not rebuilt'
    (tmp_path / 'baserom.z64').write_bytes(altered)
    cfg['sha1'] = hashlib.sha1(altered).hexdigest()
    with pytest.raises(ForkError):
        splatrefs.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, consoles.get('n64'))
    assert not (tmp_path / 'objdiff.json').exists()


def test_an_unsupported_console_leaves_generation_to_manual_setup(tmp_path):
    project = ForkProject(tmp_path)
    cfg = config(tmp_path)
    cfg['options']['platform'] = 'unknown-platform'
    (tmp_path / 'splat.yaml').write_text(yaml.safe_dump(cfg))
    assert not project.generate_objdiff()
    assert not (tmp_path / 'objdiff.json').exists()


def test_plan_accepts_source_extension_in_object_name_and_rejects_ambiguity(tmp_path):
    cfg = config(tmp_path)
    short = tmp_path / 'build/src/demo.o'
    long = tmp_path / 'build/src/demo.c.o'
    short.rename(long)
    assert splatrefs.plan(tmp_path, cfg)[0]['base'] == long
    short.write_bytes(b'stale object')
    with pytest.raises(ForkError, match='ambiguous built objects'):
        splatrefs.plan(tmp_path, cfg)


@pytest.mark.skipif(toolchains.installed('binutils-mips') is None, reason='needs pinned MIPS binutils')
def test_assembly_units_are_visible_without_counting_original_assembly_as_c(tmp_path):
    cfg = synthetic_project(tmp_path, 'n64')
    cfg['segments'][0]['subsegments'][0][1] = 'asm'
    assert splatrefs.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, consoles.get('n64'))
    entry = json.loads((tmp_path / 'objdiff.json').read_text())['units'][0]
    assert entry['name'] == 'asm/demo'
    assert 'base_path' not in entry and 'metadata' not in entry
    assert (tmp_path / entry['target_path']).is_file()


def test_verified_rebuild_retains_nested_config_version(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    data = b'full original cartridge'
    image = tmp_path/'baserom.jp.z64'
    image.write_bytes(data)
    elf = tmp_path/'build/demo.elf'
    elf.write_bytes(b'linked ELF read by mocked objcopy')
    (tmp_path/'build/demo.map').write_text(' .text 0x80000400 0x10 build/src/demo.o\n')
    (tmp_path/'asm').mkdir()
    (tmp_path/'build/asm').mkdir()
    (tmp_path/'src/demo.s').write_text('assembly alternative for the same linked C object')
    (tmp_path/'asm/start.s').write_text('glabel boot\n  jr $ra\n  nop\n')
    (tmp_path/'build/asm/start.o').write_bytes(b'linked original assembly object')
    (tmp_path/'asm/microcode.s').write_text('.incbin "program.bin"\n')
    (tmp_path/'build/asm/microcode.o').write_bytes(b'whole program blob')
    with (tmp_path/'build/demo.map').open('a') as f:
        f.write(' .text 0x80000410 0x8 build/asm/start.o\n .text 0x80000418 0x80 build/asm/microcode.o\n')
    elf.touch()

    bindir = tmp_path/'tools'
    bindir.mkdir()
    (bindir/'mips-ps2-decompals-objcopy').touch()
    monkeypatch.setattr(toolchains, 'require', lambda _: bindir)
    monkeypatch.setattr(splatrefs, 'run', lambda cmd, *a, **kw: __import__('pathlib').Path(cmd[-1]).write_bytes(data))
    project = ForkProject(tmp_path)
    selected = tmp_path/'config/jp/demo.jp.yaml'
    monkeypatch.setattr(project, '_splat_version_configs', lambda: {'jp':selected})
    assert splatrefs.verified_rebuild(project, selected, cfg, image, hashlib.sha1(data).hexdigest(), lambda *_: None)
    result = json.loads((tmp_path/'objdiff.json').read_text())
    assert result['custom_args'] == ['VERSION=jp']
    asm = next(u for u in result['units'] if u['name'] == 'asm/start')
    assert 'base_path' not in asm and 'source_path' not in asm['metadata']
    assert asm['metadata']['assembly_source'] == 'asm/start.s'
    for unit in result['units']:
        for key in ('name', 'base_path', 'target_path'):
            assert '\\' not in unit.get(key, '')
        for value in unit.get('metadata', {}).values():
            assert '\\' not in value
    assert len(result['units']) == 2
    assert '1 linked binary assembly' in result['coverage_notes'][0]


def test_padding_fallback_still_requires_a_byte_exact_full_rebuild(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    image = tmp_path/'baserom.z64'
    image.write_bytes(b'original cartridge')
    cfg.update(sha1=hashlib.sha1(image.read_bytes()).hexdigest())
    cfg['options']['target_path'] = 'baserom.z64'
    cfg['segments'][0]['subsegments'].insert(1, [8, 'pad'])
    (tmp_path/'build/demo.elf').write_bytes(b'nonmatching ELF')
    bindir = tmp_path/'tools'
    bindir.mkdir()
    (bindir/'mips-ps2-decompals-objcopy').touch()
    monkeypatch.setattr(toolchains, 'require', lambda _: bindir)
    monkeypatch.setattr(splatrefs, 'run', lambda cmd, *a, **kw: __import__('pathlib').Path(cmd[-1]).write_bytes(b'wrong'))
    with pytest.raises(ForkError, match='complete binary matches'):
        splatrefs.generate(ForkProject(tmp_path), tmp_path/'demo.yaml', cfg, consoles.get('n64'))
    assert not (tmp_path/'objdiff.json').exists()
