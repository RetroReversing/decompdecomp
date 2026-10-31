"""Small deterministic hypotheses before the random permuter's near-miss cutoff.

These are candidate rewrites, not equivalence proofs. Only the ordinary compiler and exact judge may keep
one. Work is restricted to the requested definition; strings, comments and preprocessor lines are opaque.
"""

import re

from .. import m2cfix, seeds
from ..forks.csource import _blank, find
from . import (
    address_association_repairs,
    branch_layout_repairs,
    branch_locals,
    call_initialization_repairs,
    callee_prototype_repairs,
    captured_control_repairs,
    captured_repairs,
    compiler_shape_repairs,
    evidence_repairs,
    expression_repairs,
    float_intermediate_repairs,
    global_store_repairs,
    inferred_layout_repairs,
    integer_lifetime_repairs,
    lifetime_repairs,
    load_pair_repairs,
    local_widening_repairs,
    local_width_repairs,
    masked_reuse_repairs,
    mmio_repairs,
    near_match_repairs,
    near_repairs,
    pattern_repairs,
    pointer_cast_repairs,
    register_repairs,
    register_reuse_repairs,
    scheduling_repairs,
    sdk_shape_repairs,
    stack_padding_repairs,
    stack_repairs,
    storage_scope_repairs,
    store_address_repairs,
    structure_return_repairs,
)

AGENT_FAMILIES = ('arithmetic_operand_order', 'call_forwarded_arguments', 'small_copy', 'typed_record_address', 'one_shot_scope', 'one_shot_unwrap',
                  'pointer_block_scope', 'array_address_share', 'combined_integer_lifetimes', 'inferred_record_layout',
                  'float_product_intermediate','pointer_chase_reuse','independent_assignment_order',
                  'terminal_early_return','member_store_replay','scalar_global_address','split_mask_stores')

LIMIT = 20
_IDENT = r'[A-Za-z_]\w*'
_MEMBER = rf'{_IDENT}(?:\s*(?:->|\.)\s*{_IDENT})*'


def variants(source: str, name: str, arch: str, asm: str = '', flags=(), labels=None, *, limit=LIMIT, header='') -> list[str]:
    span = find(source, name)
    if span is None:
        return []
    start, end = span
    clean = _blank(source)
    opening = clean.find('{', start, end)
    if opening < 0 or '#' in clean[opening:end]:
        return []
    body = clean[opening + 1:end].rstrip()
    if not body.endswith('}'):
        return []
    body = body[:-1]
    offset = opening + 1
    proposals = []
    labels = labels if labels is not None else {}
    for family,text in global_store_repairs.variants(source,name,header):
        proposals.append(text)
        labels[text]=family
    for family, text in captured_control_repairs.variants(source,name):
        proposals.append(text)
        labels[text] = family
    for family, text in float_intermediate_repairs.variants(source, name):
        proposals.append(text)
        labels[text] = family
    for family, text in structure_return_repairs.variants(source, name, header) + sdk_shape_repairs.variants(source, name, header):
        proposals.append(text)
        labels[text] = family
    for family, text in (stack_repairs.variants(source, name) if arch in {"mips", "ppc", "x86", "arm", "thumb"} else []):
        proposals.append(text)
        labels[text] = family
    for family, text in register_repairs.variants(source, name, arch):
        proposals.append(text)
        labels[text] = family
    for family, text in inferred_layout_repairs.variants(source, name, arch):
        proposals.append(text)
        labels[text] = family
    for family, text in compiler_shape_repairs.variants(source, name, arch, flags, header):
        proposals.append(text)
        labels[text] = family
    for family, text in evidence_repairs.variants(source, name, header):
        proposals.append(text)
        labels[text] = family
    for family, text in (address_association_repairs.variants(source, name) + call_initialization_repairs.variants(source, name)
                         + masked_reuse_repairs.variants(source, name) + register_reuse_repairs.variants(source, name)):
        proposals.append(text)
        labels[text] = family
    for text in mmio_repairs.variants(source, name, arch):
        proposals.append(text)
        labels[text] = 'mmio_address'
    for family, text in near_match_repairs.variants(source, name, asm):
        proposals.append(text)
        labels[text] = family
    for family, text in captured_repairs.variants(source, name):
        proposals.append(text)
        labels[text] = family
    for text in load_pair_repairs.variants(source,name):
        proposals.append(text)
        labels[text]='paired_load_order'
    for family,text in lifetime_repairs.variants(source,name):
        proposals.append(text)
        labels[text]=family
    for text in store_address_repairs.variants(source, name, arch):
        proposals.append(text)
        labels[text] = 'store_address_lifetime'
    for text in stack_padding_repairs.variants(source, name):
        proposals.append(text)
        labels[text] = 'stack_padding'
    for text in local_width_repairs.variants(source, name):
        proposals.append(text)
        labels[text] = 'local_width'
    for text in local_widening_repairs.variants(source, name):
        proposals.append(text)
        labels[text] = 'local_widening'
    for module in (callee_prototype_repairs, integer_lifetime_repairs, branch_layout_repairs):
        for family, text in module.variants(source, name):
            proposals.append(text)
            labels[text] = family
    # Two disjoint lifetime merges can be useful together even when either allocation is worse.
    # Keep the bundle tiny; each merge retains the existing conservative liveness boundaries.
    merges = [text for family, text in integer_lifetime_repairs.variants(source, name)
              if family == 'merge_integer_lifetimes']
    for first in merges[:2]:
        second = next((text for family, text in integer_lifetime_repairs.variants(first, name)
                       if family == 'merge_integer_lifetimes' and text != source), None)
        if second:
            proposals.append(second)
            labels[second] = 'combined_integer_lifetimes'
    for text in pointer_cast_repairs.variants(source, name, arch):
        proposals.append(text)
        labels[text] = 'pointer_cast_inline'
    # Fresh m2c drafts already get this alternative in autopilot. Saved/transferred drafts need the
    # same chance, with their macro typedef block kept out of the refinement's type substitutions.
    if arch == 'mips' and ('M2C_FIELD(' in source or 'M2C_UNK' in source or re.search(r'\barg[0-3]\b',source)):
        plain = re.sub(r'#ifndef M2C_DEFINES.*?#endif\n?', '', source, flags=re.S)
        refined = m2cfix.refine_draft(plain, name, asm)
        if refined and refined != plain:
            proposals.append(seeds.M2C_MACROS + refined)
            labels[proposals[-1]] = 'm2c_refinement'
    for family, text in scheduling_repairs.variants(source, name):
        proposals.append(text)
        labels[text] = family
    if local := branch_locals.variant(source, name):
        proposals.insert(0, local)
        labels[local] = 'branch_locals'
    for text in storage_scope_repairs.variants(source, name, arch, flags):
        proposals.append(text)
        labels[text] = 'pointer_storage_scope'
    for family,text in pattern_repairs.variants(source,name,any(re.fullmatch(r'-g[1-3]?',f) for f in flags)):
        proposals.append(text)
        labels[text] = family
    proposals += expression_repairs.variants(source, name, arch)
    proposals += near_repairs.variants(source, name, arch, asm)

    def add(a, b, text):
        candidate = source[:a] + text + source[b:]
        if candidate != source and candidate not in proposals:
            proposals.append(candidate)

    # A result initialized to zero and assigned in a single conditional arm. Require the whole body,
    # one comparison and one assignment: declarations or side effects elsewhere must not be discarded.
    early = re.fullmatch(
        rf'\s*(?:s32|int|long)\s+(?P<v>{_IDENT})\s*;\s*(?P=v)\s*=\s*0\s*;\s*'
        rf'if\s*\(\s*(?P<x>{_IDENT})\s*(?P<op>>=|<=|>|<)\s*0\s*\)\s*{{\s*'
        rf'(?P=v)\s*=\s*(?P<expr>[^;{{}}]+);\s*}}\s*return\s+(?P=v)\s*;\s*', body)
    if early and not re.search(r'\b' + re.escape(early['v']) + r'\b', early['expr']):
        inverse = {'>=': '<', '<=': '>', '>': '<=', '<': '>='}[early['op']]
        expr = source[offset + early.start('expr'):offset + early.end('expr')]
        add(offset, offset + len(body),
            f'\n    if ({early["x"]} {inverse} 0) return 0;\n    return {expr};\n')

    # Byte pointers converted to integer addresses can need the operands exchanged too. The 32-bit cast
    # is only proposed on supported 32-bit processors, never on a 64-bit or unknown imported project.
    if arch in {'mips', 'ppc', 'x86', 'arm', 'thumb'}:
        address = re.compile(rf'\(\s*(?P<wrap>\()?\s*\(\s*(?:u8|char|unsigned\s+char)\s*\*\s*\)\s*'
                             rf'(?P<p>{_MEMBER})\s*(?(wrap)\))\s*\+\s*(?P<i>{_IDENT})\s*\)')
        for m in address.finditer(body):
            add(offset + m.start(), offset + m.end(), f'({m["i"]} + (int){source[offset + m.start("p"):offset + m.end("p")]})')

    # Try dropping a trailing literal or identifier from a call, not an arbitrary expression with side effects. Walk
    # balanced parentheses so nested calls/commas are not confused with the outer argument boundary.
    macros = set(re.findall(r'^\s*#\s*define\s+(\w+)\(', clean, re.M))
    for m in re.finditer(rf'\b({_IDENT})\s*\(', body):
        if m[1] in {'if', 'while', 'for', 'switch', 'sizeof', '_Alignof'} | macros:
            continue
        depth, commas, close = 1, [], None
        for pos in range(m.end(), len(body)):
            char = body[pos]
            depth += (char in '([') - (char in ')]')
            if depth == 1 and char == ',':
                commas.append(pos)
            if depth == 0:
                close = pos
                break
        if close is None or not commas:
            continue
        tail = body[commas[-1] + 1:close].strip()
        if re.fullmatch(rf'(?:0[xX][0-9a-fA-F]+[uUlL]*|[0-9]+[uUlL]*|{_IDENT})', tail):
            add(offset + commas[-1], offset + close, '')
        if len(proposals) >= LIMIT:
            break
    for text in proposals:
        labels.setdefault(text,'expression_or_near_repair')
    # Round-robin families so a new family cannot crowd existing repairs out of a short pass.
    groups = {}
    from .repair_preflight import invalid_call
    for text in dict.fromkeys(proposals):
        if text == source:
            continue
        if invalid_call(text, name):
            continue
        groups.setdefault(labels[text],[]).append(text)
    ordered = []
    while any(groups.values()):
        for items in groups.values():
            if items:
                ordered.append(items.pop(0))
    return ordered if limit is None else ordered[:limit]
