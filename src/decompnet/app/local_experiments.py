"""Applicable local families share one source snapshot, batch budget and ordinary judge."""
import hashlib
import json
import re

from ..network import NetworkError
from ..project import compiler_for
from . import (
    agent_targets,
    agent_tokens,
    branch_layout_repairs,
    callee_prototype_repairs,
    captured_repairs,
    expression_repairs,
    integer_lifetime_repairs,
    lifetime_repairs,
    load_pair_repairs,
    local_widening_repairs,
    local_width_repairs,
    mmio_repairs,
    pattern_repairs,
    pointer_cast_repairs,
    register_repairs,
    repair_ranking,
    stack_padding_repairs,
    store_address_repairs,
)

FAMILIES = ('signedness','declaration_order','typed_temporary','branch_orientation','narrowing','source_lines',
            'address_temporary','constant_local','comparison_load','stack_array','loop_induction',
            'parameter_width','value_lifetime','sum_shape','call_result_inline','comparison_reuse','integer_address_order','mmio_address','call_arity','struct_byte_offset','switch_call','paired_load_order','array_index_width','split_scalar_lifetime','pointer_before_store','store_address_lifetime','stack_padding','local_width','pointer_cast_inline',
            'local_widening','callee_parameter_width','callee_return_width','split_integer_lifetime','merge_integer_lifetimes','branch_call','goto_leaf','switch_layout')


def run(net, agent, slug, function, revision, families=None, max_trials=6):
    if isinstance(max_trials,bool) or not isinstance(max_trials,int) or not 1<=max_trials<=6:
        raise NetworkError('Use max_trials from 1 to 6.')
    if families is not None and (not isinstance(families,list) or not families or len(families)>len(FAMILIES)
                                 or any(f not in FAMILIES for f in families)):
        raise NetworkError('Choose supported local families: '+', '.join(FAMILIES))
    requested = list(dict.fromkeys(families or FAMILIES))
    fn = net._function(function,slug)
    with net.attempt_turn(agent,fn):
        source,actual = agent_tokens.candidate(net,agent,slug,function)
        if actual!=revision:
            raise agent_tokens.stale(net,agent,fn,revision,source,actual)
        proj=net._project(slug);cfg=json.loads(proj['config'])
        flags=compiler_for(cfg,fn['vram']).get('flags',[]) if not net.fork_slug(slug) else []
        debug=any(re.fullmatch(r'-g[1-3]?',flag) for flag in flags)
        pools={family:[] for family in requested}
        for family in ('signedness','declaration_order'):
            if family not in pools:
                continue
            for target in agent_targets.valid_targets(source,family):
                if agent_tokens.target_tried(net,fn,revision,family,target['old']):
                    continue
                for patch in agent_tokens.family_patches(family,target['old']):
                    edited=agent_targets.replace_anchor(source,patch['old'],patch['new'])
                    pools[family].append(edited)
        for family,candidate in pattern_repairs.variants(source,fn['name'],debug):
            if family in pools:
                pools[family].append(candidate)
        for family,candidate in register_repairs.variants(source,fn['name'],proj['arch']):
            if family in pools:
                pools[family].append(candidate)
        for family,candidate in captured_repairs.variants(source,fn['name']):
            if family in pools:
                pools[family].append(candidate)
        for family,candidate in lifetime_repairs.variants(source,fn['name']):
            if family in pools:
                pools[family].append(candidate)
        for module in (callee_prototype_repairs, integer_lifetime_repairs, branch_layout_repairs):
            for family, candidate in module.variants(source, fn['name']):
                if family in pools:
                    pools[family].append(candidate)
        if 'local_widening' in pools:
            pools['local_widening'] = local_widening_repairs.variants(source, fn['name'])
        if 'pointer_cast_inline' in pools:
            pools['pointer_cast_inline'] = pointer_cast_repairs.variants(source,fn['name'],proj['arch'])
        if 'local_width' in pools:
            pools['local_width'] = local_width_repairs.variants(source,fn['name'])
        if 'stack_padding' in pools:
            pools['stack_padding'] = stack_padding_repairs.variants(source,fn['name'])
            measured = getattr(net,'_agent_outcomes',{}).get((agent.id,fn['id']))
            if measured and measured.get('source') == source:
                pools['stack_padding'] += stack_padding_repairs.grow(source,fn['name'],proj['arch'],measured.get('rows') or [])
        if 'store_address_lifetime' in pools:
            pools['store_address_lifetime'] = store_address_repairs.variants(source,fn['name'],proj['arch'])
        if 'paired_load_order' in pools:
            pools['paired_load_order']=load_pair_repairs.variants(source,fn['name'])
        if 'mmio_address' in pools:
            pools['mmio_address'] = mmio_repairs.variants(source,fn['name'],proj['arch'])
        if 'narrowing' in pools:
            masks=lambda text:len(re.findall(r'&\s*0x(?:[fF]{4}|[fF]{2})\b',text))
            pools['narrowing'] += [s for s in expression_repairs.variants(source,fn['name'],proj['arch']) if masks(s)<masks(source)]
        from .repair_preflight import invalid_call
        pools = {f: [s for s in pool if not invalid_call(s, fn['name'])] for f, pool in pools.items()}
        durable_scope=agent_tokens.target_scope(net,fn,'')
        pools={f:[s for s in dict.fromkeys(pool) if s!=source and not agent_tokens.compiled_before(net,fn,s,durable_scope) and not agent_tokens.target_tried(net,fn,revision,'local:'+f,s)] for f,pool in pools.items()}
        ranked = repair_ranking.order(pools,repair_ranking.evidence(net,fn['id'],agent.id))
        effect_scope=agent_tokens.target_scope(net,fn,'')
        ranked=repair_ranking.demote(net,effect_scope,ranked)
        pools = {f:pools[f] for f in ranked}
        selected=[];seen=set()
        while len(selected)<max_trials and any(pools.values()):
            for family,pool in pools.items():
                if pool and len(selected)<max_trials:
                    candidate=pool.pop(0)
                    if candidate not in seen:
                        selected.append((family,candidate));seen.add(candidate)
        if not selected:
            return f'No untried applicable local repairs. Revision {actual}; no compile run.'
        scope=agent_tokens.target_scope(net,fn,revision)
        baseline=getattr(net,'_agent_outcomes',{}).get((agent.id,fn['id']))
        if baseline and baseline.get('source')!=source:
            baseline=None
        result=agent_tokens.variants(net,agent,slug,function,revision,[{'old':source,'new':s} for _,s in selected], 'local repairs')
        observations=getattr(net,'_local_trial_observations',{}).get((agent.id,fn['id']),[])
        for (family,_), measured in zip(selected,observations):
            repair_ranking.remember(net,effect_scope,family,baseline,measured)
        for (family,candidate),measured in zip(selected,observations):
            if not measured or measured.get('score') is None:
                continue
            with net.store.tx() as db:
                db.execute('INSERT OR IGNORE INTO agent_family_targets VALUES(?,?,?,?)',
                           (fn['id'],scope,'local:'+family,hashlib.sha256(candidate.encode()).hexdigest()))
        return 'Local families: '+', '.join(f'{i}: {f}' for i,(f,_) in enumerate(selected,1))+'\n'+result
