"""Offline regression replay of exported repair examples. No network, database writes or match adoption.

The recorded successful C is the compiler-output control, not a replacement ROM/objdiff target. Output
recovery is a regression result; it never counts as a new verified game match.
"""
import argparse
import hashlib
import json
import tempfile
import time
from collections import Counter
from io import BytesIO
from pathlib import Path

from elftools.elf.elffile import ELFFile

from .. import consoles, scoring, toolchains
from ..compiler import compile_source
from ..elfobj import extract_function
from ..project import compiler_config
from ..repair_feedback import validated_provenance
from . import draft_repairs


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def rewrite_evidence(evidence):
    """A final rewrite name is causal evidence only with the corresponding base and candidate hashes."""
    provenance=validated_provenance(evidence.get('provenance'),(evidence.get('after') or {}).get('source_sha256')) or {}
    if provenance.get('kind')=='inherited repair context':
        return {'kind':'inherited','direct':False}
    if provenance.get('kind')!='random rewrite':
        return {'kind':provenance.get('kind','unknown'),'direct':False}
    direct=bool(provenance.get('source_sha256') and provenance.get('base_source_sha256') and
                provenance['base_source_sha256']==(evidence.get('before') or {}).get('source_sha256') and
                evidence.get('comparable_settings'))
    return {'kind':'random rewrite','family':provenance.get('family'),'lineage':provenance.get('lineage',[]),
            'direct':direct,'note':'Replay still required; the final rewrite name alone does not isolate a cause.'}


def replay(example, *, trials=24, seconds=120, measure=None, root=None, check_cancel=lambda:None, progress=lambda value:None):
    """Replay one frozen case; injectable measurement permits the same runner with a copied project judge."""
    if not 1 <= trials <= 128 or not 1 <= seconds <= 600:
        raise ValueError('Use 1–128 trials and 1–600 seconds per example.')
    evidence = example['evidence']
    report = {'id':example.get('id'), 'function':evidence['function'], 'trials':[], 'summary':{},
              'rewrite_evidence':rewrite_evidence(evidence)}
    before, after = evidence.get('before') or {}, evidence.get('after') or {}
    settings = after.get('settings') or {}
    for stage in (before, after):
        source, header = stage.get('source'), stage.get('header')
        if not source or header is None or not stage.get('source_sha256') or digest(source)!=stage['source_sha256']:
            return report | {'skipped':'Missing or changed source/header evidence; export full examples.'}
        expected = (stage.get('settings') or {}).get('context_sha256')
        if expected and digest(header) != expected:
            return report | {'skipped':'Captured header hash does not match.'}
    if not evidence.get('comparable_settings') or before.get('settings') != settings or before['header'] != after['header']:
        return report | {'skipped':'Compiler/build context changed; isolate that change before source replay.'}
    profile = settings.get('compiler') or {}
    platform = consoles.get(settings.get('platform'))
    name = evidence['function']
    if measure is None:
        if settings.get('mode') != 'rom':
            return report | {'skipped':'Imported projects need a copied build and diagnostic judge; standalone exports lack their build inputs.'}
        if not profile.get('toolchain') or 'cmd' in profile or not toolchains.installed(profile['toolchain']):
            return report | {'skipped':'The captured pinned compiler must already be installed; replay never downloads tools.'}
        # Only registry compilers and flags are used; exported arbitrary commands/paths cannot run.
        cfg = compiler_config(Path(root or '.'), {'compiler':{'toolchain':profile['toolchain'], 'flags':profile.get('flags',[])}})
        def measure(source):
            result = compile_source(cfg, after['header'], source)
            if not result.ok:
                return {'error':result.log[:500]}
            obj = extract_function(result.obj, name)
            # Relocation fingerprints alone do not include the values of floating constants/strings.
            elf = ELFFile(BytesIO(result.obj))
            data = [(section.name,section.data().hex()) for section in elf.iter_sections()
                    if section['sh_flags'] & 2 and not section['sh_flags'] & 4 and section['sh_type']!='SHT_NOBITS']
            return {'object':obj,'data':digest(repr(data))} if obj else {'error':'Compiled object does not define the captured function.'}
    deadline = time.monotonic()+seconds
    check_cancel()
    baseline = measure(before['source'])
    check_cancel()
    control = measure(after['source'])
    check_cancel()
    if baseline.get('error') or control.get('error'):
        return report | {'skipped':'Baseline/control compile failed.', 'baseline':baseline.get('error'), 'control':control.get('error')}
    target = control['object']
    baseline_score = scoring.diff(target, baseline['object']).score
    report.update(baseline_score=baseline_score, verdict='compiler-output regression only; no linked/objdiff match adoption')
    if target.fingerprint() == baseline['object'].fingerprint():
        return report | {'skipped':'Failed and successful sources produce the same normalized control output; no isolated regression.'}
    labels = {}
    candidates = draft_repairs.variants(before['source'],name,platform.arch,evidence.get('target_asm') or '',
                                        profile.get('flags',[]),labels,limit=None,header=after['header'])
    # Round-robin families so a long first family cannot consume the whole replay allowance.
    pools = {}
    for candidate in candidates:
        pools.setdefault(labels.get(candidate,'source_shape'),[]).append(candidate)
    counts = Counter()
    while any(pools.values()) and len(report['trials']) < trials and time.monotonic() < deadline:
        for family, pool in pools.items():
            if not pool or len(report['trials']) >= trials or time.monotonic() >= deadline:
                continue
            source = pool.pop(0)
            check_cancel()
            measured = measure(source)
            check_cancel()
            row = {'family':family,'source_sha256':digest(source)}
            if measured.get('error'):
                row.update(outcome='compile_error',diagnostic=measured['error'])
            else:
                obj = measured['object']
                score = scoring.diff(target,obj).score
                outcome = ('control_output_recovered' if obj.fingerprint()==target.fingerprint() and measured.get('data')==control.get('data') else
                           'improved' if score < baseline_score else 'neutral' if score==baseline_score else 'regressed')
                row.update(outcome=outcome,score=score)
            counts[row['outcome']] += 1
            report['trials'].append(row)
            progress({'tested':len(report['trials']),'budget':trials,'family':family,'outcome':row['outcome']})
    report.update(summary=dict(counts), remaining=sum(map(len,pools.values())))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence',type=Path,help='Full private repair-example export from the app')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--ids',type=int,nargs='+')
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--trials',type=int,default=24)
    parser.add_argument('--seconds',type=int,default=120)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error('Use --limit 1–100.')
    data = json.loads(args.evidence.read_text())
    examples = data.get('examples',[data])
    selected = [e for e in examples if not args.ids or e['id'] in args.ids][:args.limit]
    results = []
    with tempfile.TemporaryDirectory(prefix='decomp-repair-replay-') as root:
        for case in selected:
            try:
                results.append(replay(case,trials=args.trials,seconds=args.seconds,root=root))
            except (ValueError,KeyError,OSError,toolchains.ToolchainError) as error:
                results.append({'id':case.get('id'),'skipped':str(error)[:500]})
    args.output.write_text(json.dumps({'schema_version':1,'examples':results},indent=2)+'\n')
    print(f'Replayed {len(results)} examples; results saved to {args.output}. No game progress changed.')


if __name__ == '__main__':
    main()
