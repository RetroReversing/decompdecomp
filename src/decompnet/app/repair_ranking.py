"""Rank applicable hypotheses by observed mismatches without excluding other families."""

def order(families, rows):
    from .permuter_focus import classify
    focus = classify(rows)
    markers = {row[0] for row in rows if row[0] != ' '}
    if focus == 'call':
        preferred = ('call_forwarded_arguments', 'call_arity', 'callee_parameter_width', 'callee_return_width', 'call_result_inline', 'parameter_width', 'parameter_storage')
    elif markers and markers <= {'r', 's'}:
        preferred = ('stack_padding', 'unused_scalar_local', 'local_storage', 'parameter_storage',
                     'local_declaration_order', 'stack_array', 'parameter_width', 'declaration_order') if 's' in markers else (
            'constant_register_reuse', 'copy_register_reuse', 'last_shift_reuse', 'unsigned_assignment_reuse', 'table_slot_lifetime', 'store_address_lifetime', 'pointer_cast_inline', 'comparison_reuse', 'integer_address_order',
            'arithmetic_operand_order', 'typed_temporary', 'value_lifetime', 'split_integer_lifetime', 'merge_integer_lifetimes',
            'split_scalar_lifetime', 'array_index_width', 'local_width', 'declaration_order')
    elif markers & {'<', '>', '|', 'o'}:
        preferred = ('paired_load_order', 'pointer_before_store', 'guard_forward_jump', 'branch_call', 'goto_leaf', 'switch_layout',
                     'branch_orientation', 'loop_induction', 'callee_parameter_width', 'call_result_inline', 'parameter_width')
    elif 'i' in markers:
        preferred = ('local_widening', 'integer_address_order', 'signedness', 'narrowing', 'constant_local')
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


def remember(net, scope, family, baseline, measured):
    # Displayed instruction identity is only advisory evidence of ineffectiveness, never proof
    # of identical linked bytes. Errors/missing evidence do not count. Source text stays exact.
    if not baseline or not measured or baseline.get('rows') is None or measured.get('rows') is None:
        return
    if baseline.get('score') is None or measured.get('score') is None:
        return
    history=getattr(net,'_repair_effects',{})
    net._repair_effects=history
    key=(scope,family)
    unchanged=(baseline['score']==measured['score'] and baseline['rows']==measured['rows'])
    history[key]=history.get(key,0)+1 if unchanged else 0
    while len(history)>256:
        del history[next(iter(history))]


def demote(net, scope, families):
    history=getattr(net,'_repair_effects',{})
    return sorted(families,key=lambda family:history.get((scope,family),0)>=2)
