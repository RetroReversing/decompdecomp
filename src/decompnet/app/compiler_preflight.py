"""Optional local compiler checks before a helper starts a paid session."""
from ..network import NetworkError
from .agent_workbench import preview


def run(manager,s):
    net=manager.net
    if net.fork_slug(s.slug):
        raise NetworkError('Installed-compiler comparisons are currently available for ROM projects only.')
    upcoming=preview(manager,s.id,1)
    name=upcoming['current'] or (upcoming['next'][0]['name'] if upcoming['next'] else None)
    if not name: return
    proj=net.project_row(s.slug)
    fn=net._function(name,s.slug)
    if not net._source_of(fn): return
    prior=net.compiler_test_result(proj,fn)
    if prior and not prior['stale']: return
    manager._set(s,'starting',f'Compiler preflight: {name} (local, no AI tokens)')
    def log(message):
        s.push('info',str(message))
    def check():
        if s.stop.is_set():
            raise NetworkError('Compiler preflight stopped.')
    log.check_cancel=check
    net.run_compiler_comparison(s.slug,log=log,function=name)
