"""Conservative source anchors and compact family suggestions; no compilation or source writes."""

import re

from ..network import NetworkError

TYPE = r'(?:(?:unsigned|signed)\s+)?(?:int|short|long|char|float|double|[su](?:8|16|32|64))'
DECL = re.compile(r'^([ \t]*'+TYPE+r'\s+\*?\s*([A-Za-z_]\w*)\s*;)[ \t]*$', re.M)


def declarations(source):
    # Hide strings and comments while preserving positions so examples inside them aren't choices.
    masked = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                    lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), source, flags=re.S)
    return [(m[2], m.start(1), m.end(1)) for m in DECL.finditer(masked)
            if DECL.fullmatch(source[m.start(1):m.end(1)])]


def choices(source):
    items = declarations(source)
    return ('Simple declaration choices: '+', '.join(f'{n} (line {source.count(chr(10),0,a)+1})' for n,a,b in items[:6])
            if items else 'No simple uninitialized declaration choices found; supply an exact expression/declaration anchor.')


def named_anchor(source, name, family):
    items = declarations(source)
    found = [i for i,d in enumerate(items) if d[0] == name]
    if len(found) != 1:
        raise NetworkError('Declaration name must identify exactly one simple declaration. '+choices(source))
    i = found[0]; _,start,end = items[i]
    if family == 'declaration_order':
        if (i+1 == len(items) or source[end:items[i+1][1]].strip()
                or source[end:items[i+1][1]].count('\n') != 1):
            raise NetworkError('Choose a declaration immediately followed by another simple declaration. '+choices(source))
        end = items[i+1][2]
    return source[start:end]


def replace_anchor(source, old, new, occurrence=0):
    if not isinstance(old,str) or not old or not isinstance(new,str):
        raise NetworkError('Supply nonempty old text occurring exactly once and replacement new text.')
    positions = [m.start() for m in re.finditer(re.escape(old), source)]
    if isinstance(occurrence,bool) or not isinstance(occurrence,int) or occurrence < 0:
        raise NetworkError('occurrence must be a positive 1-based integer, or 0 for a unique anchor.')
    if not positions or (occurrence == 0 and len(positions) != 1) or occurrence > len(positions):
        lines = ', '.join(str(source.count('\n',0,p)+1) for p in positions[:6])
        raise NetworkError(f'Old text must occur exactly once unless occurrence is supplied. Found {len(positions)} occurrences'
                           + (f' on lines {lines}; select occurrence=1..{len(positions)}.' if positions else '. '+choices(source)))
    start = positions[(occurrence or 1)-1]
    return source[:start]+new+source[start+len(old):]


def suggestions(source):
    items = declarations(source)
    result = []
    for i,(name,start,end) in enumerate(items):
        if not re.match(r'\s*(?:float|double)\b',source[start:end]):
            result.append(f'signedness(declaration={name})')
        if (i+1 < len(items) and not source[end:items[i+1][1]].strip()
                and source[end:items[i+1][1]].count('\n') == 1):
            result.append(f'declaration_order(declaration={name})')
        if len(result)>=2:
            break
    return 'Local families available: '+', '.join(result[:2])+'. Use compile_family with the candidate revision.\n' if result else ''
