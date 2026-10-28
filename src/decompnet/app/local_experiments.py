"""Applicable local families share one source snapshot, batch budget and ordinary judge."""
import hashlib
import json
import re

from ..network import NetworkError
from ..project import compiler_for
from . import agent_targets, agent_tokens, expression_repairs, pattern_repairs, register_repairs

FAMILIES = ('signedness','declaration_order','typed_temporary','branch_orientation','narrowing','source_lines',
            'address_temporary','constant_local','comparison_load','stack_array','loop_induction',
            'parameter_width','value_lifetime','sum_shape','call_result_inline','comparison_reuse','integer_address_order')


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
        anchors={}
        for family in ('signedness','declaration_order'):
            if family not in pools:
                continue
            for target in agent_targets.valid_targets(source,family):
                if agent_tokens.target_tried(net,fn,revision,family,target['old']):
                    continue
                for patch in agent_tokens.family_patches(family,target['old']):
                    edited=agent_targets.replace_anchor(source,patch['old'],patch['new'])
                    pools[family].append(edited)
                    anchors[family,edited]=target['old']
        for family,candidate in pattern_repairs.variants(source,fn['name'],debug):
            if family in pools:
                pools[family].append(candidate)
        for family,candidate in register_repairs.variants(source,fn['name'],proj['arch']):
            if family in pools:
                pools[family].append(candidate)
        if 'narrowing' in pools:
            masks=lambda text:len(re.findall(r'&\s*0x(?:[fF]{4}|[fF]{2})\b',text))
            pools['narrowing'] += [s for s in expression_repairs.variants(source,fn['name'],proj['arch']) if masks(s)<masks(source)]
        pools={f:[s for s in dict.fromkeys(pool) if s!=source and not agent_tokens.target_tried(net,fn,revision,'local:'+f,s)] for f,pool in pools.items()}
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
        result=agent_tokens.variants(net,agent,slug,function,revision,[{'old':source,'new':s} for _,s in selected], 'local repairs')
        submitted=re.search(r'last submitted variant (\d+)',result)
        for family,candidate in selected[:int(submitted[1]) if submitted else 0]:
            with net.store.tx() as db:
                db.execute('INSERT OR IGNORE INTO agent_family_targets VALUES(?,?,?,?)',
                           (fn['id'],scope,'local:'+family,hashlib.sha256(candidate.encode()).hexdigest()))
                if (family,candidate) in anchors:
                    db.execute('INSERT OR IGNORE INTO agent_family_targets VALUES(?,?,?,?)',
                               (fn['id'],scope,family,hashlib.sha256(anchors[family,candidate].encode()).hexdigest()))
        return 'Local families: '+', '.join(f'{i}: {f}' for i,(f,_) in enumerate(selected,1))+'\n'+result
