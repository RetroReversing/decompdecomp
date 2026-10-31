"""Advisory source/type checks: no inferred identity or SDK body is accepted as a verdict."""
import re

from ..forks.csource import _blank, find


def hints(source, name, asm='', arch=''):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    body = clean[span[0]:span[1]]
    result = []
    if arch == 'mips':
        from ..arch.mips_runtime import helper, specialized_calls
        if kind := helper(asm):
            result.append('Target instructions suggest an O32 64-bit runtime helper ('+kind+'). '
                          'Check 64-bit argument/return widths and bounded MIPS III -32 -O1 profiles before mutating 32-bit drafts.')
        if specialized_calls(asm):
            result.append('A caller-saved temporary appears live across a call. Whole-unit/static-helper clobber analysis '
                          'may use a specialized calling convention. Check argument registers and helper context; '
                          'isolated ordinary-ABI permutations or adding helper bodies may not reproduce it.')
    if 'M2C_FIELD(' in body or re.search(r'\*\s*\(\s*\([^)]*\*\s*\)\s*\(', body):
        result.append('Check raw offset casts against real record declarations and argument types. '
                      'Use an explicitly configured local source/SDK template before more permutations; preserve its macros, locals and line breaks.')
    if re.search(r'\*[^;{}\n]+\*', body):
        result.append('Check multiplication order, intermediate widths and signedness against the source and assembly. '
                      'Do not reassociate floating-point products or add casts merely to raise similarity.')
    if re.search(r'\bif\s*\(', body):
        result.append('Reference source may differ from retail: verify branch exits, allocation-failure paths and cleanup stores against the ROM. '
                      'Source identity alone is not an exact-match verdict.')
    return result
