"""Bounded source experiments learned from repair evidence; the exact judge decides every result."""

import re

from ..forks.csource import _blank, find

IDENT = r'[A-Za-z_]\w*'
TYPE = r'(?:void\s*\*|[su](?:8|16|32)|unsigned\s+(?:char|short|int)|char|short|int)'


def close(text, start, left='(', right=')'):
    depth = 0
    for pos in range(start,len(text)):
        depth += (text[pos] == left) - (text[pos] == right)
        if depth == 0:
            return pos+1
    return None


def variants(source, name, debug_lines=False):
    span = find(source,name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{',*span)
    if opening < 0 or '#' in clean[opening:span[1]]:
        return []
    body = clean[opening:span[1]]
    result = []
    def add(family, edits):
        text = source
        for a,b,replacement in sorted(edits,reverse=True):
            text = text[:opening+a]+replacement+text[opening+b:]
        if text != source and text not in [v for _,v in result]:
            result.append((family,text))

    # Swap braced branches and negate the comparison; no dangling-else reconstruction.
    branches = []
    for m in list(re.finditer(r'\bif\s*\(',body))[:24]:
        end = close(body,m.end()-1)
        if not end:
            continue
        arm = re.match(r'\s*{',body[end:])
        if not arm:
            continue
        a = end+arm.end()-1; b = close(body,a,'{','}')
        other = re.match(r'\s*else\s*{',body[b:]) if b else None
        if not other:
            continue
        c = b+other.end()-1; d = close(body,c,'{','}')
        cond = body[m.end():end-1]
        simple = re.fullmatch(r'\s*([^()=;{}]+?)\s*(==|!=|<=|>=|<|>)\s*([^()=;{}]+?)\s*',cond)
        if not d or not simple:
            continue
        inverse = {'==':'!=','!=':'==','<=':'>','>=':'<','<':'>=','>':'<='}[simple[2]]
        newcond = f'{simple[1]} {inverse} {simple[3]}'
        branches.append((b-a+d-c,[(m.end(),end-1,newcond),(a,b,source[opening+c:opening+d]),(c,d,source[opening+a:opening+b])]))
    for _,edits in sorted(branches,key=lambda item:item[0])[:4]:
        add('branch_orientation',edits)

    # Narrow stores: introduce a same-width local in the existing block rather than retain an inline cast.
    store = re.compile(r'(?P<lhs>\*\s*\(\s*(?P<t>[su](?:8|16)|unsigned\s+(?:char|short))\s*\*\s*\)\s*'+IDENT+r')\s*=\s*(?P<rhs>[^;{}]+);')
    for m in list(store.finditer(body))[:2]:
        rhs = source[opening+m.start('rhs'):opening+m.end('rhs')].strip()
        rhs = re.sub(r'^\(\s*'+re.escape(m['t'])+r'\s*\)\s*','',rhs)
        variable = '__repair_narrow'
        if variable in clean:
            continue
        lhs = source[opening+m.start('lhs'):opening+m.end('lhs')]
        add('typed_temporary',[(m.start(),m.end(),f"{m['t']} {variable} = {rhs}; {lhs} = {variable};")])

    # Hoist a dereferenced argument immediately ahead of a preceding simple store. Never cross
    # another call or control-flow statement, and retain the typed load exactly as submitted.
    call = re.compile(r'(?P<store>\b'+IDENT+r'\s*=\s*'+IDENT+r'\s*;)\s*\b'+IDENT+r'\s*\(')
    for m in list(call.finditer(body))[:2]:
        end = close(body,m.end()-1)
        if not end:
            continue
        expr = source[opening+m.end():opening+end-1].strip()
        typed = re.match(r'\*\s*\(\s*('+TYPE+r')\s*\*\s*\)',expr)
        if not typed or ',' in expr or re.search(r'\+\+|--|\bvolatile\b|\b'+IDENT+r'\s*\(',body[m.end():end-1]):
            continue
        var = '__repair_argument'
        if var in clean:
            continue
        add('typed_temporary',[(1,1,f'\n{typed[1]} {var};\n'),(m.start(),m.start(),f'{var} = {expr};\n'),(m.end(),end-1,var)])
        # Reuse an already declared compatible local when it has not been used yet and is
        # assigned again before its later uses. This also keeps old C89 compilers happy.
        type_pattern = re.escape(typed[1]).replace(r'\ ',r'\s*')
        for decl in re.finditer(type_pattern+rf'\s*({IDENT})\s*;',body[:m.start()]):
            local = decl[1]
            if len(re.findall(r'\b'+re.escape(local)+r'\b',body[:m.start()])) != 1:
                continue
            next_use = re.search(r'\b'+re.escape(local)+r'\b',body[end:])
            if next_use and re.match(r'\s*=',body[end+next_use.end():]):
                add('typed_temporary',[(m.start(),m.start(),f'{local} = {expr};\n'),(m.end(),end-1,local)])
                break

    if debug_lines:
        # Imported build flags are not known here: callers leave this phase off until per-file
        # invocation metadata is available (ROADMAP: debug-line text repairs for imported builds).
        # This text-only phase preserves/debug-label experiments the AST printer cannot represent.
        assignment = re.compile(r'(?m)^[ \t]*('+IDENT+r')\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*;[ \t]*\n')
        for m in list(assignment.finditer(body))[:2]:
            suffix = body[m.end():]
            use = re.search(r'(?m)^[ \t]*[^;{}\n]+\*\s*'+re.escape(m[1])+r'\s*;',suffix)
            if use and not re.search(r'[{}]|\b'+re.escape(m[1])+r'\b',suffix[:use.start()]):
                at = m.end()+use.start()
                statement = source[opening+m.start():opening+m.end()].strip()
                add('source_lines',[(m.start(),m.end(),''),(at,at,statement+' ')])
        # Join/split adjacent statement lines without changing token order.
        for m in list(re.finditer(r';[ \t]*\n[ \t]*(?='+IDENT+r'\s*=)',body))[:2]:
            add('source_lines',[(m.start(),m.end(),'; ')])
    return result[:10]
