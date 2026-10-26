"""Give generated ROM-derived, source-less reference units C candidate files using the project's own build."""

from __future__ import annotations

import copy
import json
from pathlib import Path

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


def prepare(project, log=print, *, limit: int = 32) -> list[str]:
    """Prepare a bounded batch of source-less units with app-verified reference objects.

    Probe the actual project's object rule before publishing each candidate. Never invent a compiler or alter
    Makefiles/linker scripts. Rebuilt references may use the project's original assembly paths.
    Unsupported builds retain their source-less units and original config; later calls continue the batch.
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
            layout = next((refs / name for name in ('objects', 'targets')
                           if refs / name in target.parents), None)
            if layout is None or not target.is_file():
                continue
            relative = target.relative_to(layout).with_suffix('')
            if layout.name == 'targets':
                value = (entry.get('metadata') or {}).get('assembly_source')
                if not isinstance(value, str) or Path(value).is_absolute():
                    continue
                assembly = (project.root / value).resolve()
                if project.root.resolve() not in assembly.parents:
                    continue
            else:
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
            entry['metadata'] = {**entry.get('metadata', {}), 'source_path': source.relative_to(project.root).as_posix(),
                                 'autopilot_scaffold': True, 'draft_assembly': assembly.relative_to(project.root).as_posix()}
            entry['base_path'] = 'build/' + source.relative_to(project.root).as_posix() + '.o'
            unit = project._units_of({'units': [entry]})[0]
            log(f'Preparing C candidate: {source.relative_to(project.root)}')
            ok, output = project.build_unit(unit)
            if not ok or not unit.base.is_file():
                raise ForkError(f"This project's build cannot compile the generated C candidate {unit.source}:\n{output[-2000:]}")
            sources.append(unit.source)
            if len(sources) >= limit:
                break  # Probe bounded batches; subsequent runs prepare the next source-less units.
        if sources:
            (project.root / 'objdiff.json').write_text(json.dumps(cfg, indent=2) + '\n', encoding='utf-8')
    except BaseException:
        (project.root / 'objdiff.json').write_bytes(original)
        for source in made:
            source.unlink(missing_ok=True)
            (project.root / ('build/' + source.relative_to(project.root).as_posix() + '.o')).unlink(missing_ok=True)
        raise
    return sources
