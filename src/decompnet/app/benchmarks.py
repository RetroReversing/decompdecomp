"""Local comparison checkpoints and sequential, isolated trials. Never merge results implicitly."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .. import agents as kinds
from .. import limits
from ..hostenv import NO_WINDOW
from ..network import NetworkError
from .agent_limits import validate


def write(path, data):
    path=Path(path)
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,indent=2),encoding='utf-8')
    tmp.chmod(0o600)
    tmp.replace(path)


def read(path):
    try: return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError): return {}


def remap(value, old, new):
    if isinstance(value,str):
        pairs=[(str(old),str(new))]
        if hasattr(old,'as_posix'): pairs.append((old.as_posix(),new.as_posix()))
        for before,after in set(pairs):
            value=re.sub(re.escape(before)+r'''(?=$|[/\\\s"'():,;])''',lambda match,replacement=after:replacement,value)
        return value
    if isinstance(value,list): return [remap(v,old,new) for v in value]
    if isinstance(value,dict): return {k:remap(v,old,new) for k,v in value.items()}
    return value


def files(root):
    return {str(p.relative_to(root)):(p.stat().st_size,p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file() and '.git' not in p.relative_to(root).parts}


def target_digest(target, relocs, size, insns):
    return hashlib.sha256(repr((bytes(target or b''),relocs,size,insns)).encode()).hexdigest()


def tree_digest(root):
    digest=hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if not path.is_file(): continue
        digest.update(str(path.relative_to(root)).encode())
        with path.open('rb') as stream:
            while chunk:=stream.read(1024*1024): digest.update(chunk)
    return digest.hexdigest()


def relocate(database, old, new):
    """Rewrite project paths and textual build metadata, without links back to the original tree."""
    conn=sqlite3.connect(database)
    try:
        for pid,root,cfg in conn.execute('SELECT id,root,config FROM projects').fetchall():
            conn.execute('UPDATE projects SET root=?,config=? WHERE id=?',
                         (str(new),json.dumps(remap(json.loads(cfg),old,new)),pid))
        conn.commit()
    finally: conn.close()
    for path in new.rglob('*'):
        if not path.is_file() or path.stat().st_size>8*1024*1024: continue
        try: text=path.read_text(encoding='utf-8')
        except (UnicodeError,OSError): continue
        if '\0' in text: continue
        if path.suffix=='.json':
            try:
                data=json.loads(text)
                replaced=remap(data,old,new)
                if replaced!=data: path.write_text(json.dumps(replaced,indent=2),encoding='utf-8')
                continue
            except ValueError: pass
        replaced=remap(text,old,new)
        # Ninja escapes spaces and Windows drive colons even when JSON uses normal paths.
        for before,after in [(str(old),str(new)),(old.as_posix(),new.as_posix())]:
            escaped=before.replace(':','$:').replace(' ','$ ')
            if escaped!=before:
                replacement=after.replace(':','$:').replace(' ','$ ')
                replaced=re.sub(re.escape(escaped)+r'''(?=$|[/\\\s"'():,;])''',lambda match,value=replacement:value,replaced)
        if replaced!=text: path.write_text(replaced,encoding='utf-8')


def stamp(net,pid):
    # Includes source, targets, configuration and context; a mutation during copying invalidates the checkpoint.
    payload=[]
    for table in ('projects','functions','context_versions','symbols','notes','attempts'):
        if table=='projects': rows=net.store.all('SELECT * FROM projects WHERE id=?',pid)
        elif table=='attempts': rows=net.store.all('SELECT t.* FROM attempts t JOIN functions f ON f.id=t.function_id WHERE f.project_id=? ORDER BY t.id',pid)
        else: rows=net.store.all(f'SELECT * FROM {table} WHERE project_id=?',pid)
        payload.append([list(r) for r in rows])
    return hashlib.sha256(repr(payload).encode()).hexdigest()


class Benchmarks:
    def __init__(self,app):
        self.app=app
        self.root=app.home/'comparisons'
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.active={}
        self.lock=threading.Lock()

    def folder(self, identifier):
        if not isinstance(identifier,str) or len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):
            raise NetworkError('Choose a saved comparison checkpoint.')
        folder=self.root/identifier
        if not (folder/'checkpoint.json').is_file(): raise NetworkError('Checkpoint not found.')
        return folder

    def inventory(self,slug):
        out=[]
        for path in sorted(self.root.glob('*/checkpoint.json'),key=lambda p:p.stat().st_mtime,reverse=True):
            item=read(path)
            if item.get('slug')!=slug: continue
            report=read(path.parent/'report.json')
            if report.get('state')=='running' and path.parent.name not in self.active:
                report['state']='interrupted'
            history=[read(p) for p in sorted((path.parent/'reports').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)]
            for old in history:
                if old.get('state')=='running' and (path.parent.name not in self.active or old.get('id')!=report.get('id')):
                    old['state']='interrupted'
            visible={k:v for k,v in item.items() if k!='baseline'}
            out.append(visible | {'report':report,'reports':history[:20]})
        return out

    def checkpoint(self,slug,log=print):
        net=self.app.net
        proj=net.project_row(slug)
        if slug in net.links or slug in net.rooms:
            raise NetworkError('Leave the shared room before making a local comparison checkpoint.')
        if any(s.slug==slug and s.state in ('starting','working','waiting') for s in self.app.manager.sessions.values()):
            raise NetworkError('Stop this project’s agents before creating a comparison checkpoint.')
        if net.store.one('SELECT 1 FROM claims c JOIN functions f ON f.id=c.function_id WHERE f.project_id=? AND c.expires_at>?',proj['id'],time.time()):
            raise NetworkError('This project still has active work. Wait for its checks to finish before taking a checkpoint.')
        source=Path(proj['root']).resolve()
        if not source.is_dir(): raise NetworkError('The project folder is missing.')
        if self.root.resolve().is_relative_to(source):
            raise NetworkError('The project folder contains comparison storage. Move the project into its own folder first.')
        initial=stamp(net,proj['id'])
        file_state=files(source)
        identifier=uuid.uuid4().hex
        folder=self.root/identifier
        folder.mkdir(mode=0o700)
        try:
            log('Copying project files into a private checkpoint…')
            shutil.copytree(source,folder/'project',symlinks=False,ignore=shutil.ignore_patterns('.git'))
            target=sqlite3.connect(folder/'checkpoint.db')
            try:
                # SQLite's backup observes a consistent committed snapshot without pausing the live writer.
                origin=sqlite3.connect(Path(net.store.path).resolve().as_uri()+'?mode=ro',uri=True)
                try: origin.backup(target)
                finally: origin.close()
                target.execute('PRAGMA foreign_keys=OFF')
                target.execute('DELETE FROM projects WHERE id!=?',(proj['id'],))
                target.execute('DELETE FROM functions WHERE project_id!=?',(proj['id'],))
                tables=[r[0] for r in target.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                for table in tables:
                    columns={r[1] for r in target.execute(f'PRAGMA table_info("{table}")')}
                    if 'project_id' in columns: target.execute(f'DELETE FROM "{table}" WHERE project_id!=?',(proj['id'],))
                    if 'project' in columns: target.execute(f'DELETE FROM "{table}" WHERE project!=?',(slug,))
                    if 'source_project_id' in columns: target.execute(f'DELETE FROM "{table}" WHERE source_project_id!=?',(proj['id'],))
                    if 'source_function_id' in columns: target.execute(f'DELETE FROM "{table}" WHERE source_function_id NOT IN (SELECT id FROM functions)')
                    if 'function_id' in columns: target.execute(f'DELETE FROM "{table}" WHERE function_id NOT IN (SELECT id FROM functions)')
                for table in ('claims','helpers','helper_messages'):
                    if table in tables: target.execute(f'DELETE FROM "{table}"')
                target.execute("DELETE FROM settings WHERE key!='abandons_recounted'")  # no room connections, provider credentials or live worker state
                target.commit()
                target.execute('VACUUM')
                target.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            finally: target.close()
            if initial!=stamp(net,proj['id']) or file_state!=files(source):
                raise NetworkError('The project changed while copying. Stop background work and create the checkpoint again.')
            relocate(folder/'checkpoint.db',source,folder/'project')
            snapshot=sqlite3.connect(folder/'checkpoint.db')
            try: baseline={str(r[0]):{'score':r[1],'matched':r[2]=='matched','eligible':r[2]!='asm','size':r[3] or 0,'insns':r[4] or 0,'target_digest':target_digest(r[5],r[6],r[3],r[4])} for r in snapshot.execute('SELECT id,best_score,status,size,n_insns,target,relocs FROM functions')}
            finally: snapshot.close()
            digest=hashlib.sha256((folder/'checkpoint.db').read_bytes()).hexdigest()
            item={'id':identifier,'slug':slug,'created_at':time.time(),'digest':digest,'files_digest':tree_digest(folder/'project'),'source_stamp':initial,
                  'kind':json.loads(proj['config']).get('kind','rom'),'baseline':baseline}
            write(folder/'checkpoint.json',item)
            log('Checkpoint ready. Every trial will receive a fresh copy; no agents have started.')
            return {'id':identifier,'digest':digest}
        except BaseException:
            shutil.rmtree(folder,ignore_errors=True)
            raise

    def run(self,identifier,plans,budget,selection,allow_builds=False,log=print):
        folder=self.folder(identifier)
        checkpoint=read(folder/'checkpoint.json')
        if hashlib.sha256((folder/'checkpoint.db').read_bytes()).hexdigest()!=checkpoint['digest'] or tree_digest(folder/'project')!=checkpoint['files_digest']:
            raise NetworkError('The saved checkpoint has changed. Create a new checkpoint before comparing models.')
        if checkpoint['kind']=='fork' and not allow_builds:
            raise NetworkError('Imported-project trials run the copied project’s build scripts with your account permissions. Enable that option to continue.')
        if not isinstance(plans,list) or not 2<=len(plans)<=6 or any(not isinstance(p,dict) for p in plans):
            raise NetworkError('Choose between two and six model trials.')
        caps=validate(budget)
        if not caps.get('seconds') and not caps.get('tokens'):
            raise NetworkError('Set an equal time or token budget for all trials.')
        # A finite wall-clock ceiling prevents unknown CLI usage from leaving a token-only trial running forever.
        caps.setdefault('seconds',1800)
        policy=self.app.net.validate_selection(selection)
        if any(s.state in ('starting','working','waiting') for s in self.app.manager.sessions.values()):
            raise NetworkError('Stop the app’s other agents before running a controlled model comparison.')
        options=[]
        for plan in plans:
            kind=kinds.get(str(plan.get('kind','')))
            opts={'model':str(plan.get('model','')).strip(),'limits':caps,'selection':policy,'per_session':8}
            if not opts['model']: raise NetworkError('Choose an explicit model ID for each trial.')
            if kind.uses_endpoints:
                from .support import session_options
                endpoint=self.app.settings.endpoint(str(plan.get('endpoint','')))
                if not endpoint: raise NetworkError('Choose a configured API endpoint for each API trial.')
                opts |= session_options(endpoint)
                opts['model']=str(plan['model']).strip()
            options.append((kind.id,opts))
        with self.lock:
            if identifier in self.active: raise NetworkError('This checkpoint already has a comparison running.')
            stopped=threading.Event()
            self.active[identifier]={'stop':stopped,'process':None,'folder':None}
        from .. import __version__
        report={'app_version':__version__,'build_id':getattr(self.app,'build_id',''),'id':uuid.uuid4().hex,'state':'running','checkpoint':identifier,'digest':checkpoint['digest'],'budget':caps,'selection':policy,
                'started_at':time.time(),'trials':[]}
        def save_report():
            archive=folder/'reports'
            archive.mkdir(exist_ok=True,mode=0o700)
            write(archive/(report['id']+'.json'),report)
            write(folder/'report.json',report)
        save_report()
        try:
            for index,(kind,opts) in enumerate(options):
                cancel=getattr(log,'check_cancel',lambda:None)
                cancel()
                if stopped.is_set(): break
                trial=folder/'runs'/uuid.uuid4().hex
                trial.mkdir(parents=True,mode=0o700)
                log(f'Preparing trial {index+1}/{len(options)}: {opts["model"]}')
                report['active_trial']={'index':index+1,'total':len(options),'model':opts['model']}
                save_report()
                shutil.copytree(folder/'project',trial/'project',symlinks=False)
                shutil.copyfile(folder/'checkpoint.db',trial/'trial.db')
                relocate(trial/'trial.db',folder/'project',trial/'project')
                cancel()
                if stopped.is_set(): break
                env=dict(os.environ,DECOMP_DETECT_AGENTS='off',DECOMP_WINDOW='0',DECOMP_TRAY='0')
                with (trial/'worker.log').open('w',encoding='utf-8') as output:
                    # Shutdown must see either no process or a registered child and stop marker.
                    with self.lock:
                        if stopped.is_set(): break
                        self.active[identifier]['folder']=trial
                        proc=subprocess.Popen(limits.capped([sys.executable,'-m','decompnet.app.benchmark_worker',str(trial)]),
                                              stdin=subprocess.PIPE,stdout=output,stderr=subprocess.STDOUT,text=True,
                                              env=env,start_new_session=True,creationflags=NO_WINDOW)
                        self.active[identifier]['process']=proc
                    proc.stdin.write(json.dumps({'slug':checkpoint['slug'],'kind':kind,'options':opts,'baseline':checkpoint['baseline']}))
                    proc.stdin.close()
                    deadline=time.monotonic()+caps['seconds']+180
                    while proc.poll() is None:
                        try: cancel()
                        except BaseException:
                            stopped.set()
                            raise
                        if stopped.is_set() or time.monotonic()>deadline:
                            (trial/'stop').touch()
                            try: proc.wait(timeout=45)
                            except subprocess.TimeoutExpired:
                                from .agents import _terminate
                                _terminate(proc)
                            break
                        time.sleep(0.5)
                    result=read(trial/'result.json') or {'state':'failed','error':'The trial did not produce a report. Inspect its local worker log.'}
                report['trials'].append(result | {'kind':kind,'model':opts['model'],'folder':str(trial)})
                save_report()
                log(f'Trial {index+1}: {result.get("matches",0)} new matches; {result.get("improved",0)} functions improved.')
            report['state']='cancelled' if stopped.is_set() else 'complete'
            if any(t.get('state')=='failed' for t in report['trials']): report['state']='failed'
        except BaseException as e:
            stopped.set()
            if (current:=self.active[identifier].get('folder')): (current/'stop').touch()
            report['state']='failed'
            report['error']=str(e)
            raise
        finally:
            active=self.active.get(identifier,{})
            child=active.get('process')
            if child is not None and child.poll() is None:
                if active.get('folder'): (active['folder']/'stop').touch()
                try: child.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    from .agents import _terminate
                    _terminate(child)
            report.pop('active_trial',None)
            report['ended_at']=time.time()
            save_report()
            with self.lock: self.active.pop(identifier,None)
        # Show exclusive matches without implying they were merged into the working project.
        for trial in report['trials']:
            others={n for t in report['trials'] if t is not trial for n in t.get('matched_ids',[])}
            trial['unique_matches']=[name for ident,name in zip(trial.get('matched_ids',[]),trial.get('matched_functions',[])) if ident not in others]
        save_report()
        return report

    def stop(self,identifier=None):
        with self.lock:
            for key,active in self.active.items():
                if identifier is None or key==identifier:
                    active['stop'].set()
                    if active.get('folder'): (active['folder']/'stop').touch()

    def shutdown(self):
        self.stop()
        with self.lock: children=[a.get('process') for a in self.active.values()]
        for proc in children:
            if proc is None or proc.poll() is not None: continue
            try: proc.wait(timeout=45)
            except subprocess.TimeoutExpired:
                from .agents import _terminate
                _terminate(proc)
