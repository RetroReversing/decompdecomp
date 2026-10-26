"""Small typed-expression hypotheses; only the exact judge can accept their scheduling effects."""
import re

from ..forks.csource import _blank, find

IDENT = r'[A-Za-z_]\w*'
INTEGER = r'(?:s32|u32|int|unsigned\s+int)'


def variants(source, name):
    span = find(source,name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{',*span)
    body = clean[opening:span[1]]
    if '#' in body:
        return []
    result = []

    def add(edits):
        text = source
        merged = {}
        for a,b,replacement in edits:
            merged[a,b] = merged.get((a,b),'')+replacement
        for (a,b),replacement in sorted(merged.items(),reverse=True):
            text = text[:opening+a]+replacement+text[opening+b:]
        if text not in result and text != source:
            result.append(text)

    # A casted floating multiply inside a call, immediately after a store. Moving the computation
    # ahead of that store is useful when aliasing prevents the compiler doing so itself. Do not move
    # calls, volatile accesses or an expression across a block boundary. These remain hypotheses.
    expr = re.compile(r'\(\s*(f64|double|f32|float)\s*\)\s*'+IDENT+r'\s*\*\s*'+IDENT)
    for m in list(expr.finditer(body))[:2]:
        start = max(body.rfind(';',0,m.start()),body.rfind('{',0,m.start()))+1
        statement = body[start:m.start()]
        if re.match(r'\s*(?:if|while|for|switch|return|sizeof)\b',statement) or not re.match(r'\s*'+IDENT+r'\s*\(',statement):
            continue
        previous = re.search(r'(?P<store>[^;{}]+=[^;{}]+;)\s*$',body[:start])
        if not previous or 'volatile' in body or '__repair_value' in clean:
            continue
        # Only a single assignment; comparisons/compound assignments aren't store anchors.
        store = previous['store']
        if re.search(r'==|!=|<=|>=|[+*/&|^]=-?|\+\+|--',store):
            continue
        store_lhs,store_rhs = store.split('=',1)
        if re.search(r'\b'+IDENT+r'\s*\(',store_rhs):
            continue
        if re.fullmatch(r'\s*'+IDENT+r'\s*',store_lhs) and re.search(r'\b'+re.escape(store_lhs.strip())+r'\b',m[0]):
            continue
        rhs = source[opening+m.start():opening+m.end()]
        # Two declaration placements cover common allocator differences without a broad search.
        first_decl = re.match(r'\s*'+IDENT+r'\s+'+IDENT+r'\s*;',body[1:])
        placements = [1,1+first_decl.end()] if first_decl else [1]
        for at in placements:
            add([(at,at,f'\n    {m[1]} __repair_value;'),
                 (previous.start('store'),previous.start('store'),f'\n        __repair_value = {rhs};'),
                 (m.start(),m.end(),'__repair_value')])

    # Split a scalar parameter assignment through an existing local of the same type. A later read
    # of that local could observe the overwrite: allow only a later plain assignment or no later use.
    signature = clean[span[0]:opening]
    for m in list(re.finditer(r'\b('+IDENT+r')\s*=\s*([^;{}]+\*[^;{}]+);',body))[:12]:
        lhs,rhs = m[1],m[2]
        param = re.search(r'\b('+INTEGER+r')\s+'+re.escape(lhs)+r'\s*[,)]',signature)
        if not param or re.search(r'\b'+IDENT+r'\s*\(|\+\+|--|=',rhs):
            continue
        for decl in list(re.finditer(r'\b'+re.escape(param[1])+r'\s+('+IDENT+r')\s*;',body[:m.start()]))[:3]:
            local = decl[1]
            if local == lhs or re.search(r'\b'+re.escape(local)+r'\b',rhs):
                continue
            use = re.search(r'\b'+re.escape(local)+r'\b',body[m.end():])
            if use and not re.match(r'\s*=(?!=)',body[m.end()+use.end():]):
                continue
            expr_text = source[opening+m.start(2):opening+m.end(2)]
            add([(m.start(),m.end(),f'{local} = {expr_text}; {lhs}={local};')])
    return result[:6]
