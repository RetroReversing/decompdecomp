"""ZCode, Z.AI's agent CLI (the ZCode desktop app bundles it; a `zcode` launcher on PATH is what the app finds).
Each session runs `zcode app-server`: the ZCode Protocol, one JSON object a line both ways — the one agent here that
answers back on stdin. decompdecomp speaks it through the feed kinds this module returns and app/agents.py acts on:

- `stdin(prompt)` writes the session's opening script: `session/create` (the app's MCP server, session-scoped, with
  the token in its Authorization header; the model and thought level) with the session id already chosen, then
  `session/send` with the prompt. stdin stays open: the server asks back.
- every question the server asks (`session/requestRuntimePreferences`, plugin servers wanting auth) goes back as a
  (("stdin", line) feed item, and ("stop", line) ends the session once the turn is done.
- what the model says arrives as `session/event` notifications whose `payload` carries one event: text and thinking
  stream in `part.delta` pieces gathered per step (like Antigravity's), a tool call is a `part.upserted` whose part
  is a tool, and `turn.completed` brings what the session spent.

A session runs on ZCode's own model selection. The launcher offers zcode's default (empty) and any of its picker ids
("provider/model", exactly what its `/model` shows, with an optional "#effort" after it). ZCode 0.17 has no way to
list them from outside, so none are offered up front.
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from . import SAID_CHARS, Agent, args_brief, error_text, tool_name, usage_item

TOOL_ALLOW = "mcp__decomp__*"  # the app's tools and nothing else: no shell, no file edits, no web, no plugin tools
MCP_TIMEOUT_MS = 180_000  # one tool call may take this long (a fork's build)
SESSION_ID = "sess-decomp-"  # the one session each run creates, in zcode's own database
_NO_MODEL = "Select a model before sending a message"


class _PerSession(threading.local):
    """What one session's start needs later in `stdin`, on the same helper thread (`session_command` runs there)."""

    mcp_url = ""
    token = ""
    model = ""
    workdir = ""


_here = _PerSession()


def _provider_config_env() -> dict[str, str]:
    """The desktop app ships the CLI's built-in provider registry in its versioned runtime folder, not beside the CLI
    script a `zcode` launcher runs (a helper then starts five times and gives up, saying 无法定位 CLI ZCode Built-in
    Provider Config). The newest copy is handed to the session, with the user's own personal provider config beside it
    — the CLI takes the two only together."""
    builtin = sorted((Path.home() / ".zcode" / "v2" / "runtime" / "provider")
                     .glob("*/*/endpoint-*/zcode-builtin.json"), key=lambda p: p.stat().st_mtime)
    personal = Path.home() / ".zcode" / "v2" / "provider_config.json"
    if not builtin or not personal.is_file():
        return {}
    return {"ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": str(builtin[-1]),
            "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE": str(personal)}


def selection(model: str) -> dict | None:
    """ZCode's picker id as the session's model selection: "account:zai-start-plan/GLM-5.3-Flash#high" says provider,
    model and thought level. A bare name stays zcode's own default: only its picker knows the provider."""
    model, _, effort = model.partition("#")
    if "/" not in model:
        return None
    provider, _, name = model.partition("/")
    picked: dict = {"providerId": provider, "modelId": name}
    if effort:
        picked["options"] = {"reasoningLevel": effort}
    return picked


class Zcode(Agent):
    id = "zcode"
    name = "ZCode"
    command = "zcode"
    vendor = "Z.AI"
    install = ("mkdir -p ~/.local/bin && printf '#!/bin/sh\\nexec node \"/Applications/ZCode.app/Contents/Resources"
               "/glm/zcode.cjs\" \"$@\"\\n' > ~/.local/bin/zcode && chmod +x ~/.local/bin/zcode")
    windows_install = ("New-Item -ItemType Directory -Force -Path \"$env:USERPROFILE\\.local\\bin\" | Out-Null; "
                       "Set-Content -Path \"$env:USERPROFILE\\.local\\bin\\zcode.cmd\" -Value "
                       "'@node \"%LOCALAPPDATA%\\Programs\\ZCode\\resources\\glm\\zcode.cjs\" %*'")
    signin = "Run `zcode login` once in a terminal (the desktop app's own sign-in doesn't carry over)."
    docs = "https://zcode.z.ai"
    takes_effort = True  # the thought level, after a "#" in the model's picker id
    keeps_stdin = True  # it speaks a protocol: the app answers its questions and ends it (("stop", line)) when done

    def session_command(self, exe: str, workdir, mcp_url: str, token: str, model: str) -> tuple[list[str], dict]:
        """`zcode app-server` speaks the ZCode Protocol on stdio. The app's MCP address, the token and the model reach
        it through `session/create` in `stdin`'s script, so they ride on the helper thread, never the command line."""
        _here.mcp_url, _here.token, _here.model = mcp_url, token, model
        _here.workdir = str(workdir)
        return [exe, "app-server"], _provider_config_env()

    def stdin(self, prompt: str) -> str:
        """The opening script: one session, created with only the app's MCP server and tools, then the prompt."""
        workdir = _here.workdir
        model, _, effort = _here.model.partition("#")
        create: dict = {"id": 1, "method": "session/create", "params": {
            "sessionId": SESSION_ID + uuid.uuid4().hex[:20],
            "workspace": {"workspacePath": workdir, "workspaceIdentity": "decompdecomp", "workspaceKey": workdir},
            "mode": "yolo",  # a background helper can't ask; it may use only decompdecomp's tools (TOOL_ALLOW)
            "mcpServers": [{"name": "decomp", "type": "http", "url": _here.mcp_url,
                            "headers": [{"name": "Authorization", "value": f"Bearer {_here.token}"}],
                            "isolation": "session", "alwaysLoad": True, "timeoutMs": MCP_TIMEOUT_MS}],
            "toolAllowlist": [TOOL_ALLOW],
            "titleGenerationEnabled": False, "dynamicWorkflowEnabled": False, "offPeakToolEnabled": False}}
        if picked := selection(_here.model):
            create["params"]["model"] = picked
        elif effort:
            create["params"]["thoughtLevel"] = effort
        send = {"id": 2, "method": "session/send",
                "params": {"sessionId": create["params"]["sessionId"], "content": prompt}}
        return json.dumps(create) + "\n" + json.dumps(send) + "\n"

    def parse(self, event: dict, state: dict) -> list[tuple[str, str] | None]:
        """The feed items in one protocol line. The server's questions go back on stdin (("stdin", line)); a finished
        turn ends the session (("stop", line)) — the app would otherwise wait for a process that never exits."""
        method = event.get("method")
        if method == "session/requestRuntimePreferences":  # keep the session as bare as a helper needs it
            return [("stdin", json.dumps({"id": event["id"], "result": {
                "nativeSearchEnhancementsEnabled": False, "toolSearchMode": "off",
                "memoryEnabled": False, "askUserQuestionAutoResolutionEnabled": False}}))]
        if method and "id" in event:  # anything else it asks (plugin servers' auth, ...) is refused: no tools but ours
            return [("stdin", json.dumps({"id": event["id"], "error": {"code": -32601, "message": "unsupported"}}))]

        if method is None and "error" in event:  # the answer to the opening script
            return [("error", error_text(event["error"])[:SAID_CHARS]), ("stop", "create failed")]

        body = event.get("payload") if method == "session/event" and isinstance(event.get("payload"), dict) else {}
        t = body.get("type")
        said: list[tuple[str, str] | None] = []
        if t == "part.delta" and body.get("field") in ("text", "reasoning"):
            field = body["field"]
            state[field] = state.get(field, "") + str(body.get("delta") or "")
        elif t == "part.upserted" and isinstance(body.get("part"), dict):
            part = body["part"]
            if part.get("type") == "tool" and part.get("callId") not in state.setdefault("tools", set()):
                state["tools"].add(part["callId"])
                said.append(("tool", f"{tool_name(str(part.get('tool') or ''))}"
                                     f"({args_brief((part.get('state') or {}).get('input'))})"))
            elif part.get("type") == "step-finish":  # the model's step is over: what it said and thought, in one piece
                said += _flush(state)
        elif t in ("turn.completed", "turn.failed"):
            said += _flush(state)
            if t == "turn.failed":
                said.append(("error", error_text(body.get("error"))[:SAID_CHARS]))
            else:
                if not state.get("text") and str(body.get("response") or "").strip():
                    said.append(("text", str(body["response"]).strip()))  # a backstop for runs with no step parts
                usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
                cache = body.get("cacheStats") if isinstance(body.get("cacheStats"), dict) else {}
                seconds = body.get("duration")
                said.append(("info", "session finished" + (f" ({seconds:.0f}s)" if isinstance(seconds, (int, float))
                                                           else "")))
                said.append(usage_item(input=usage.get("inputTokens"), output=usage.get("outputTokens"),
                                       cache_read=cache.get("cacheReadTokens"),
                                       tokens=0 if usage else body.get("tokenCount"), turns=1))
            said.append(("stop", t))
        elif t == "session.closed":
            said.append(("stop", "session closed"))
        return said

    def fatal(self, error: str) -> bool:
        # A model the registry doesn't know (a mistyped picker id) and a session that has no model to run on would
        # fail the same way every try.
        return "不存在 Model" in error or _NO_MODEL in error or "Provider Registry" in error


def _flush(state: dict) -> list[tuple[str, str]]:
    """The pieces gathered per step, as one item each ("think" first: what it thought comes before what it said)."""
    out = []
    for kind, key in (("think", "reasoning"), ("text", "text")):
        piece = str(state.pop(key, "") or "").strip()
        if piece:
            out.append((kind, piece))
    return out


ZCODE = Zcode()
