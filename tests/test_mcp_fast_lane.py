"""Regression test: ultra-fast handshake/read tools use a dedicated lane separate from claim/search tools."""
import threading

import anyio

from decompnet.app import tools
from tests.test_agent_workflow_efficiency import game


def test_fast_tools_do_not_queue_behind_saturated_quick_lane(tmp_path, monkeypatch):
    assert 'project_info' in tools.FAST_TOOLS
    assert 'get_claims' in tools.FAST_TOOLS
    assert 'claim_function' not in tools.FAST_TOOLS
    assert 'search_code' not in tools.FAST_TOOLS

    net, agent, _ = game(tmp_path)
    monkeypatch.setattr(tools, 'QUICK_LANE', 1)
    monkeypatch.setattr(tools, 'FAST_LANE', 2)

    entered = threading.Event()
    release = threading.Event()
    orig_dispatch = tools._dispatch

    def blocking_dispatch(net_, agent_, slug_, name_, args_, **kw):
        if name_ == 'search_code':
            entered.set()
            assert release.wait(timeout=5)
            return 'done'
        return orig_dispatch(net_, agent_, slug_, name_, args_, **kw)

    monkeypatch.setattr(tools, '_dispatch', blocking_dispatch)
    mcp = tools.build_mcp(net, lambda ctx: (agent, 'game'))

    async def scenario():
        results = {}

        async def slow_quick_tool():
            results['search'] = await mcp.call_tool('search_code', {'pattern': 'foo'})

        async def fast_handshake():
            await anyio.to_thread.run_sync(lambda: entered.wait(timeout=5))
            with anyio.fail_after(3):
                results['info'] = await mcp.call_tool('project_info', {'detail': False})
            release.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(slow_quick_tool)
            tg.start_soon(fast_handshake)
        return results

    res = anyio.run(scenario)
    assert 'info' in res and 'search' in res
