"""Compilers and tools (Setup), agents: installing them, finding which are installed, starting, stopping and
restarting helpers, and the settings.
"""

from __future__ import annotations

import shutil
import subprocess

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from decompnet.hostenv import NO_WINDOW

from ... import agents as kinds
from ... import linuxvm, toolchains
from ...network import NetworkError
from .. import agents
from ..support import session_options, spread_sessions
from . import App


def register(app: App) -> None:
    net, mcp, manager, jobs = app.net, app.mcp, app.manager, app.jobs
    settings, guard, body, call, fail = app.settings, app.guard, app.body, app.call, app.fail

    @mcp.custom_route("/api/sdk-library", methods=["GET", "POST"])
    async def api_sdk_library(request: Request) -> Response:
        from .. import sdk_library
        if (bad := guard(request, write=request.method == "POST")):
            return bad
        try:
            if request.method == "GET":
                return JSONResponse(await call(lambda: sdk_library.state(net, request.query_params.get('slug'))))
            data = await body(request)
            if data.get('slug'):
                await call(lambda: sdk_library.choose(net, str(data['slug']), data.get('selection')))
                from .. import sdk_names, workers
                workers.pool().submit(lambda: sdk_names.name_functions(net, str(data['slug'])))
                return JSONResponse(await call(lambda: sdk_library.state(net, str(data['slug']))))
            folders = data.get('folders', [])
            return JSONResponse({'job': jobs.start('sdk-library', lambda log: sdk_library.scan(net, folders, log))})
        except (NetworkError, ValueError, OSError) as e:
            return fail(e)

    @mcp.custom_route("/api/linux-vm", methods=["POST"])
    async def api_linux_vm(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        return JSONResponse({'job': jobs.start('linux-vm', lambda log: linuxvm.setup(log=log))})

    @mcp.custom_route("/api/toolchain", methods=["POST"])
    async def api_toolchain(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        tid, accept = str(data.get("id", "")), bool(data.get("accept_license"))
        return JSONResponse({"job": jobs.start("toolchain", lambda log: (toolchains.install(tid, accept_license=accept, force=bool(data.get("force")), log=log)
                                                                         and {"id": tid}))})

    @mcp.custom_route("/api/toolchain/all", methods=["POST"])
    async def api_toolchain_all(request: Request) -> Response:
        """Every compiler of a project's console, then its compiler test again (Engine.install_compilers)."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        slug, accept = str(data.get("slug", "")), bool(data.get("accept_license"))
        return JSONResponse({"job": jobs.start("toolchain", lambda log: net.install_compilers(slug, accept, log=log))})

    @mcp.custom_route("/api/agents/install", methods=["POST"])
    async def api_agent_install(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        try:
            kind = kinds.get(str((await body(request)).get("kind", "")))
        except ValueError:
            kind = None
        if kind is None or not kind.install_text():
            return fail(ValueError("nothing to install"))
        if kind.install_text().startswith("npm ") and not shutil.which("npm"):
            return fail(ValueError(f"{kind.name} is installed with npm: install Node.js from https://nodejs.org "
                                   "first (the LTS version), then try again"))

        def work(log):
            log(f"$ {kind.install_text()}")
            proc = subprocess.Popen(kind.install_command(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                                    creationflags=NO_WINDOW)
            for line in proc.stdout:
                log(line.rstrip())
            if proc.wait() != 0:
                raise RuntimeError(f"the installer exited with code {proc.returncode}")
            kinds.known_versions.pop(kind.id, None)
            manager.refresh_detected(wait=True)  # the launcher shows it, with its models, when the job is done
            return {"kind": kind.id}
        return JSONResponse({"job": jobs.start("agent-install", work)})

    @mcp.custom_route("/api/agents/detect", methods=["POST"])
    async def api_agents_detect(request: Request) -> Response:
        """Look again which agents are installed and their models (a sign-in since): in the background."""
        if (bad := guard(request, write=True)):
            return bad
        manager.refresh_detected()
        return JSONResponse({"detecting": True})

    @mcp.custom_route("/api/agents/start", methods=["POST"])
    async def api_agents_start(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        kind, slug = str(data.get("kind", "")), str(data.get("slug", ""))
        given = data.get("options") or {}
        options = {k: v for k, v in given.items() if k in ("model", "per_session", "task", "selection")}  # task: "readable" polishes
        options.setdefault("per_session", settings.data.get("per_session", agents.PER_SESSION))
        count = max(1, min(int(data.get("count", 1)), agents.MAX_AT_ONCE))
        picked: list[str] = []
        try:
            net.project_row(slug)
            net.warm_briefs_soon(slug)  # ready before the helpers' first claims
            if given.get("effort") in kinds.get(kind).efforts:  # an endpoint helper's (the others' rides on the model)
                options["effort"] = given["effort"]
            if kinds.get(kind).uses_endpoints:
                picked = [str(e) for e in (given.get("endpoints") or ([given["endpoint"]] if given.get("endpoint") else []))]
                plan = spread_sessions(settings, manager, picked, count, given)
                ids = [sid for entry, n in plan for sid in manager.start(kind, slug, n, options | session_options(entry))]
            else:
                ids = manager.start(kind, slug, count, options)
        except (NetworkError, ValueError) as e:
            return fail(e)
        # remembered, so the next start is one click; models typed in come back as suggestions
        model = str(options.get("model") or "")
        settings.data["launcher"] = {"kind": kind, "model": model, "count": count} | ({"endpoints": picked} if picked else {})
        if model and not kinds.get(kind).uses_endpoints:
            recent = settings.data.setdefault("recent_models", {}).setdefault(kind, [])
            settings.data["recent_models"][kind] = [model] + [m for m in recent if m != model][:7]
        settings.save()
        return JSONResponse({"started": ids})

    @mcp.custom_route("/api/agents/rename", methods=["POST"])
    async def api_agents_rename(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        try:
            label = manager.rename(str(data.get("id", "")), data.get("label", ""))
        except ValueError as e:
            return fail(e)
        return JSONResponse({"label": label})

    @mcp.custom_route("/api/agents/selection", methods=["POST"])
    async def api_agents_selection(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        try:
            policy = manager.set_selection(str(data.get("id", "")), data.get("selection") or {}, data.get("per_session"))
        except (NetworkError, ValueError) as e:
            return fail(e)
        return JSONResponse({"selection": policy})

    @mcp.custom_route("/api/agents/stop", methods=["POST"])
    async def api_agents_stop(request: Request) -> Response:
        """Stop a helper (`id`), forget it (`remove`), or stop every helper on a project (`slug`)."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        if data.get("remove"):
            await call(manager.remove, str(data.get("id")))
        elif data.get("slug"):
            await call(manager.stop_project, str(data["slug"]))
        elif data.get("id"):
            await call(manager.stop, str(data["id"]))
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/agents/restart", methods=["POST"])
    async def api_agents_restart(request: Request) -> Response:
        """Start a stopped or paused helper again."""
        if (bad := guard(request, write=True)):
            return bad
        try:
            await call(manager.restart, str((await body(request)).get("id", "")))
        except ValueError as e:
            return fail(e)
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/settings", methods=["POST"])
    async def api_settings(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        if isinstance(data.get("api"), dict):  # (before 0.10.1: the one endpoint there was) the first one
            first = settings.endpoints()[0]["id"] if settings.endpoints() else ""
            settings.save_endpoint(data["api"] | {"id": first})
        if isinstance(data.get("endpoint"), dict):
            settings.save_endpoint(data["endpoint"])
        if data.get("remove_endpoint"):
            settings.remove_endpoint(str(data["remove_endpoint"]))
            for sid, sess in list(manager.sessions.items()):  # its sessions stop with it
                if sess.options.get("endpoint") == str(data["remove_endpoint"]) and sess.state in agents.LIVE:
                    manager.stop(sid)
        if "per_session" in data:
            settings.data["per_session"] = max(1, min(int(data["per_session"]), 20))
        if "autopilot" in data:
            settings.data["autopilot"] = bool(data["autopilot"])
        if "budget_usd" in data:
            settings.data["budget_usd"] = max(0.0, float(data["budget_usd"] or 0))
        if 'iterative_enabled' in data:
            settings.data['iterative_enabled'] = bool(data['iterative_enabled'])
        if 'reuse_auto' in data:
            settings.data['reuse_auto'] = bool(data['reuse_auto'])
        for key, maximum in [('reuse_seconds', 600), ('reuse_attempts', 1024), ('iterative_seconds', 600), ('iterative_attempts', 1024)]:
            if key in data:
                settings.data[key] = max(1, min(maximum, int(data[key])))
        if any(key in data for key in ('reuse_auto', 'reuse_seconds', 'reuse_attempts', 'iterative_enabled', 'iterative_seconds', 'iterative_attempts')):
            net.cascades.configure(settings.data.get('reuse_auto', True), settings.data.get('reuse_seconds', 120),
                                   settings.data.get('reuse_attempts', 128), settings.data.get('iterative_enabled', False),
                                   settings.data.get('iterative_seconds', 120), settings.data.get('iterative_attempts', 128))
        for key, maximum in [('permuter_max_insns', 100000), ('permuter_seconds', 3600), ('permuter_max_turns', 1000)]:
            if key in data:
                settings.data[key] = max(1, min(maximum, int(data[key])))
        if any(key in data for key in ('permuter_max_insns', 'permuter_seconds', 'permuter_max_turns')):
            net.permuter.configure(settings.data)
        if "auto_update" in data:
            settings.data["auto_update"] = bool(data["auto_update"])
        if "check_updates" in data:
            settings.data["check_updates"] = bool(data["check_updates"])
        if "auto_compiler" in data:
            settings.data["auto_compiler"] = net.auto_compiler = bool(data["auto_compiler"])
        if "notes_cleared_to" in data:  # the notification centre's Clear all: events up to this id stay hidden
            settings.data["notes_cleared_to"] = max(0, int(data["notes_cleared_to"] or 0))
        if "bridges_removed_seen" in data:  # the notice of what the cleanup removed (cleanup.report), seen
            settings.data["bridges_removed_seen"] = bool(data["bridges_removed_seen"])
        if "rom_library" in data:
            settings.data["rom_library"] = str(data["rom_library"] or "").strip()
        settings.save()
        return JSONResponse(settings.public())
