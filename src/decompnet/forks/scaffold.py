"""Give generated ROM-derived, source-less reference units C candidate files using the project's own build."""

from __future__ import annotations

import copy
import json

from .. import seeds
from .project import STATE, ForkError

HEADER = '''/* C candidates for an undecompiled unit. The original ROM/assembly remains the linked baseline. */
typedef signed char s8;
typedef unsigned char u8;
typedef signed short s16;
typedef unsigned short u16;
typedef signed int s32;
typedef unsigned int u32;
typedef signed long long s64;
typedef unsigned long long u64;
typedef float f32;
typedef double f64;
''' + seeds.M2C_MACROS + '\n'


def prepare(project, log=print) -> list[str]:
    """Only touch source-less units whose target and assembly were privately generated and verified by this app.

    Probe the actual project's object rule before publishing each candidate. Never invent a compiler or alter
    Makefiles/linker scripts. Unsupported builds retain their source-less units and original config.
    """
    cfg = copy.deepcopy(project.objdiff_config())
    original = (project.root / 'objdiff.json').read_bytes()
    refs = (project.root / STATE / 'objdiff-references').resolve()
    if project.root.resolve() not in refs.parents:
        return []
    made = []
    sources = []
    try:
        for entry in cfg.get('units', []):
            if entry.get('base_path') or (entry.get('metadata') or {}).get('source_path'):
                continue
            target = (project.root / entry.get('target_path', '')).resolve()
            if refs / 'objects' not in target.parents or not target.is_file():
                continue
            relative = target.relative_to(refs / 'objects').with_suffix('')
            assembly = refs / 'asm' / relative.with_suffix('.s')
            if not assembly.is_file():
                continue
            source = project.root / 'src' / 'decompdecomp' / relative.with_suffix('.c')
            if project.root.resolve() not in source.resolve().parents:
                raise ForkError('The candidate source path leaves this checkout; no files were changed')
            if source.exists():
                raise ForkError(f'{source.relative_to(project.root)} already exists; it was retained')
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(HEADER, encoding='utf-8')
            made.append(source)
            entry['metadata'] = {**entry.get('metadata', {}), 'source_path': str(source.relative_to(project.root)),
                                 'autopilot_scaffold': True, 'draft_assembly': str(assembly.relative_to(project.root))}
            entry['base_path'] = 'build/' + str(source.relative_to(project.root)) + '.o'
            unit = project._units_of({'units': [entry]})[0]
            log(f'Preparing C candidate: {source.relative_to(project.root)}')
            ok, output = project.build_unit(unit)
            if not ok or not unit.base.is_file():
                raise ForkError(f"This project's build cannot compile the generated C candidate {unit.source}:\n{output[-2000:]}")
            sources.append(unit.source)
        if sources:
            (project.root / 'objdiff.json').write_text(json.dumps(cfg, indent=2) + '\n', encoding='utf-8')
    except BaseException:
        (project.root / 'objdiff.json').write_bytes(original)
        for source in made:
            source.unlink(missing_ok=True)
            (project.root / ('build/' + str(source.relative_to(project.root)) + '.o')).unlink(missing_ok=True)
        raise
    return sources
