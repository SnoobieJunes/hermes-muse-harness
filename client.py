"""MSP client for a local `muse serve` process.

Speaks newline-delimited JSON-RPC 2.0. Does not read Muse credentials and
does not open a connection to api.meta.ai.
"""

from __future__ import annotations

import json
import os
import queue
import secrets
import shutil
import subprocess
import threading
import time
from typing import Any, Callable

STRIPPED_ENV = (
    "META_API_KEY",
    "MODEL_API_KEY",
    "META_MODEL_API_KEY",
    "MUSE_CODE_SUB_TOKEN",
)
APPROVAL_MODES = ("denyUnmatched", "onRequest", "allowAll")
DENY_DECISIONS = ("denied", "deniedPolicyAmendment", "abort")
ALLOW_DECISION = "approved"


class MuseHarnessError(RuntimeError):
    pass


def uuid7() -> str:
    """UUIDv7. MSP command ids are rejected when they are not one."""
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = secrets.randbits(74)
    value = (ms << 80) | (0x7 << 76) | (rand & ((1 << 74) - 1))
    value = (value & ~(0x3 << 62)) | (0x2 << 62)
    h = f"{value:032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base if base is not None else os.environ)
    for key in STRIPPED_ENV:
        env.pop(key, None)
    return env


def serve_argv(
    *,
    binary: str,
    model: str,
    provider: str,
    contributor_mode: str,
) -> list[str]:
    if contributor_mode not in ("on", "off"):
        raise MuseHarnessError("contributor_mode must be on or off")
    argv = [
        binary,
        "serve",
        "--provider",
        provider,
        "--contributor-mode",
        contributor_mode,
        "--no-session-log",
    ]
    if provider in ("meta", "local"):
        argv.extend(["--model", model])
    return argv


def normalize_mcp_servers(raw: Any) -> dict[str, dict[str, Any]]:
    if raw in (None, "", {}):
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MuseHarnessError("mcp_servers is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise MuseHarnessError("mcp_servers must be an object of name to server")
    cleaned: dict[str, dict[str, Any]] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not name or not isinstance(spec, dict):
            raise MuseHarnessError(f"bad MCP server entry: {name!r}")
        transport = spec.get("transport")
        if transport == "stdio":
            command = spec.get("command")
            if not isinstance(command, str) or not command or any(c in command for c in " \t\n;|&"):
                raise MuseHarnessError(f"MCP server {name} needs a single executable command")
            args = spec.get("args") or []
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                raise MuseHarnessError(f"MCP server {name} args must be a list of strings")
            entry: dict[str, Any] = {"transport": "stdio", "command": command, "args": args}
        elif transport == "streamableHttp":
            url = spec.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                raise MuseHarnessError(f"MCP server {name} needs an http(s) url")
            entry = {"transport": "streamableHttp", "url": url}
        else:
            raise MuseHarnessError(f"MCP server {name} transport must be stdio or streamableHttp")
        cleaned[name] = entry
    return cleaned


def pick_choice(choices: list[dict[str, Any]], *, allow: bool) -> dict[str, Any] | None:
    want = ALLOW_DECISION if allow else None
    if allow:
        for choice in choices:
            if choice.get("decision") == want:
                return choice
        return None
    for choice in choices:
        if choice.get("decision") in DENY_DECISIONS:
            return choice
    return None


class LineTransport:
    def __init__(self, proc: subprocess.Popen[bytes]):
        self.proc = proc
        self.inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        try:
            for raw in self.proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    self.inbox.put(json.loads(line))
                except json.JSONDecodeError:
                    self.inbox.put({"jsonrpc": "2.0", "error": {"message": "non-json frame", "data": line[:200]}})
        finally:
            self.inbox.put(None)

    def send(self, frame: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(frame) + "\n").encode())
        self.proc.stdin.flush()

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass


def run_turn(
    *,
    prompt: str,
    model: str = "muse-wonder-full-1.0",
    workspace: str,
    approval_mode: str = "denyUnmatched",
    mcp_servers: Any = None,
    timeout_s: float = 600,
    contributor_mode: str = "off",
    provider: str = "meta",
    binary: str | None = None,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not prompt or not prompt.strip():
        raise MuseHarnessError("prompt is empty")
    if approval_mode not in APPROVAL_MODES:
        raise MuseHarnessError(f"approval_mode must be one of {', '.join(APPROVAL_MODES)}")
    if not os.path.isdir(workspace):
        raise MuseHarnessError(f"workspace does not exist: {workspace}")
    mcp = normalize_mcp_servers(mcp_servers)
    exe = binary or shutil.which("muse")
    if not exe:
        raise MuseHarnessError("muse is not on PATH. Install Muse Code, then retry.")

    proc = popen(
        serve_argv(binary=exe, model=model, provider=provider, contributor_mode=contributor_mode),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env(env),
    )
    transport = LineTransport(proc)
    text_parts: list[str] = []
    decisions: list[dict[str, str]] = []
    terminal = None
    session_id = None
    next_id = 1
    deadline = time.monotonic() + timeout_s
    allow = approval_mode == "allowAll"

    def send_request(method: str, params: dict[str, Any]) -> str:
        nonlocal next_id
        req_id = str(next_id)
        next_id += 1
        frame: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        transport.send(frame)
        return req_id

    def handle_server_request(frame: dict[str, Any]) -> None:
        method = frame.get("method")
        req_id = frame.get("id")
        params = frame.get("params") or {}
        transport.send({"jsonrpc": "2.0", "id": req_id, "result": {}})
        if method != "approval/request":
            return
        choice = pick_choice(list(params.get("availableChoices") or []), allow=allow)
        if choice is None:
            decisions.append({"approvalId": str(params.get("approvalId")), "decision": "unanswered"})
            return
        decisions.append({
            "approvalId": str(params.get("approvalId")),
            "decision": str(choice.get("decision")),
        })
        send_request("approval/decide", {
            "commandId": uuid7(),
            "sessionId": params.get("sessionId") or session_id,
            "approvalId": params.get("approvalId"),
            "requirementId": params.get("currentRequirementId"),
            "choiceId": choice.get("choiceId"),
        })

    try:
        init_id = send_request("initialize", {
            "clientInfo": {"name": "hermes_muse_harness", "version": "0.1.0"},
            "capabilities": {
                "userInputDialogs": False,
                "requestedCapabilities": ["sessionMcp"],
            },
        })
        _wait_id(transport, init_id, deadline)
        transport.send({"jsonrpc": "2.0", "method": "initialized"})
        start_params: dict[str, Any] = {
            "commandId": uuid7(),
            "approvalMode": approval_mode,
            "workspaceRoot": os.path.abspath(workspace),
        }
        if provider in ("meta", "local"):
            start_params["modelId"] = model
        if mcp:
            start_params["config"] = {"mcpServers": mcp}
        start_id = send_request("session/start", start_params)
        started = _wait_id(transport, start_id, deadline)
        session_id = started["result"]["session"]["sessionId"]
        turn_id = send_request("turn/start", {
            "commandId": uuid7(),
            "sessionId": session_id,
            "input": [{"type": "text", "text": prompt}],
        })
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MuseHarnessError("muse turn timed out")
            frame = _next_frame(transport, remaining)
            if frame.get("id") == turn_id and frame.get("error"):
                raise MuseHarnessError(frame["error"].get("message") or "turn/start failed")
            method = frame.get("method")
            if method == "item/delta":
                params = frame.get("params") or {}
                if params.get("field", "text") == "text":
                    text_parts.append(params.get("delta") or "")
            elif method == "turn/completed":
                terminal = (frame.get("params") or {}).get("terminal")
                break
            elif "method" in frame and "id" in frame:
                handle_server_request(frame)
        return {
            "session_id": session_id,
            "model": model,
            "terminal": terminal,
            "text": "".join(text_parts),
            "approvals": decisions,
        }
    finally:
        transport.close()


def _next_frame(transport: LineTransport, timeout_s: float) -> dict[str, Any]:
    try:
        frame = transport.inbox.get(timeout=timeout_s)
    except queue.Empty as exc:
        raise MuseHarnessError("muse serve closed the connection or stalled") from exc
    if frame is None:
        raise MuseHarnessError("muse serve exited before the turn finished")
    return frame


def _wait_id(transport: LineTransport, req_id: str, deadline: float) -> dict[str, Any]:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MuseHarnessError("timed out waiting for muse")
        frame = _next_frame(transport, remaining)
        if frame.get("id") != req_id:
            continue
        if frame.get("error"):
            message = frame["error"].get("message") or "muse request failed"
            raise MuseHarnessError(message)
        return frame
