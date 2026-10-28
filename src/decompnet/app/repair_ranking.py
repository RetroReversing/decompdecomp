"""Rank applicable hypotheses by observed mismatches without excluding other families."""

def order(families, rows):
    markers = {row[0] for row in rows if row[0] != ' '}
    if markers and markers <= {'r', 's'}:
        preferred = ('stack_array', 'parameter_width', 'declaration_order') if 's' in markers else (
            'comparison_reuse', 'integer_address_order', 'typed_temporary', 'value_lifetime', 'declaration_order')
    elif markers & {'<', '>', '|', 'o'}:
        preferred = ('branch_orientation', 'loop_induction', 'call_result_inline', 'parameter_width')
    elif 'i' in markers:
        preferred = ('integer_address_order', 'signedness', 'narrowing', 'constant_local')
    else:
        return list(families)
    return sorted(families, key=lambda f: preferred.index(f) if f in preferred else len(preferred))


def evidence(net, function_id, agent_id):
    diffs = getattr(net, '_last_diffs', {})
    own = diffs.get((agent_id,function_id))
    if own:
        return own[0]
    # Advisory ordering only: a saved draft may predate this process's latest diff. Never use
    # this hint as a verdict or exclude unranked families; the normal judge tests each source.
    for (_,fid), value in reversed(list(diffs.items())):
        if fid == function_id:
            return value[0]
    return []
