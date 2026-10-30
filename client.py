"""MSP client for a local `muse serve` process.

Speaks newline-delimited JSON-RPC 2.0. Does not read Muse credential files
and does not open its own connection to api.meta.ai.
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
from urllib.parse import urlparse

# Muse uses META_API_KEY before a browser login, always. These are removed on
# the subscription attempt and put back only after a typed quota failure.
API_KEY_ENV = ("META_API_KEY", "MODEL_API_KEY", "META_MODEL_API_KEY")
# Minted subscription token from other plugins. Never forward it.
BLOCKED_ENV = ("MUSE_CODE_SUB_TOKEN",)
# Child process gets this set only. Reviewer list: no TERM, no SHELL, no provider keys.
_ALLOW_EXACT = frozenset({"PATH", "HOME", "USER", "TMPDIR", "LANG"})
APPROVAL_MODES = ("denyUnmatched", "allowAll")
DENY_DECISIONS = ("denied", "deniedPolicyAmendment", "abort")
ALLOW_DECISION = "approved"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_LINE_BYTES = 8_000_000
MAX_STDERR_BYTES = 64_000
MAX_PROMPT_CHARS = 1_000_000


class MuseHarnessError(RuntimeError):
    pass


class QuotaExceeded(MuseHarnessError):
    def __init__(self, detail: dict[str, Any]):
        super().__init__("subscription allowance exhausted")
        self.detail = detail


def uuid7() -> str:
    """UUIDv7. MSP command ids are rejected when they are not one."""
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = secrets.randbits(74)
    value = (ms << 80) | (0x7 << 76) | (rand & ((1 << 74) - 1))
    value = (value & ~(0x3 << 62)) | (0x2 << 62)
    h = f"{value:032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def resolve_api_key(getter: Callable[[str], str | None]) -> str | None:
    """Hermes stores the Meta key as MODEL_API_KEY. The other two are aliases."""
    for name in ("MODEL_API_KEY", "META_API_KEY", "META_MODEL_API_KEY"):
        value = getter(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _allowed_env_key(key: str) -> bool:
    if key in BLOCKED_ENV or key in API_KEY_ENV:
        return False
    return key in _ALLOW_EXACT or key.startswith("LC_") or key.startswith("XDG_")


def child_env(
    base: dict[str, str] | None = None,
    *,
    billing: str = "subscription",
    api_key: str | None = None,
) -> dict[str, str]:
    """Build the muse child environment from an allowlist.

    subscription: no API key, so the browser login is used.
    api_key: set META_API_KEY from the explicit argument only. A key sitting
    in the parent environment is not forwarded. Muse has no switch that uses
    the key only after the cap; a set key replaces the login.
    """
    if billing not in ("subscription", "api_key"):
        raise MuseHarnessError("billing must be subscription or api_key")
    source = dict(base if base is not None else os.environ)
    env = {
        key: value
        for key, value in source.items()
        if _allowed_env_key(key) and isinstance(value, str)
    }
    if billing == "api_key":
        key = api_key.strip() if isinstance(api_key, str) else ""
        if not key:
            raise MuseHarnessError(
                "subscription allowance is exhausted and no META_API_KEY or MODEL_API_KEY is set"
            )
        env["META_API_KEY"] = key
    return env


def is_quota_failure(frame: dict[str, Any]) -> bool:
    """True only when MSP marks the failure as the quota class.

    Clients branch on the presence of error.quota, never by parsing message.
    """
    if frame.get("method") != "turn/completed":
        return False
    error = (frame.get("params") or {}).get("error")
    return isinstance(error, dict) and "quota" in error


def serve_argv(
    *,
    binary: str,
    model: str,
    provider: str,
    contributor_mode: str,
) -> list[str]:
    if contributor_mode not in ("on", "off"):
        raise MuseHarnessError("contributor_mode must be on or off")
    if provider not in ("meta", "local", "echo"):
        raise MuseHarnessError("provider must be meta, local, or echo")
    if not model or any(c in model for c in " \t\n;|&"):
        raise MuseHarnessError("model id is not a single token")
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
    """Accept only loopback HTTP MCP servers.

    Stdio is rejected: a model-supplied command is arbitrary execution once
    the tool call is approved. Remote hosts are rejected.
    """
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
        if spec.get("transport") != "streamableHttp":
            raise MuseHarnessError(f"MCP server {name} must be streamableHttp on loopback")
        url = spec.get("url")
        if not isinstance(url, str):
            raise MuseHarnessError(f"MCP server {name} needs a url")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            raise MuseHarnessError(f"MCP server {name} url is not allowed")
        if host not in LOOPBACK_HOSTS:
            raise MuseHarnessError(f"MCP server {name} must use localhost")
        if parsed.port is None or not (1 <= parsed.port <= 65535):
            raise MuseHarnessError(f"MCP server {name} needs an explicit port")
        cleaned[name] = {"transport": "streamableHttp", "url": url}
    return cleaned


def workspace_within(workspace: str, allowed_root: str) -> str:
    root = os.path.realpath(allowed_root)
    target = os.path.realpath(workspace)
    if not os.path.isdir(target):
        raise MuseHarnessError(f"workspace does not exist: {workspace}")
    try:
        if os.path.commonpath([root, target]) != root:
            raise MuseHarnessError("workspace must stay inside the session directory")
    except ValueError as exc:
        raise MuseHarnessError("workspace must stay inside the session directory") from exc
    return target


def pick_choice(choices: list[dict[str, Any]], *, allow: bool) -> dict[str, Any] | None:
    if allow:
        for choice in choices:
            if choice.get("decision") == ALLOW_DECISION:
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
        self.stderr = bytearray()
        self._out = threading.Thread(target=self._read_stdout, daemon=True)
        self._err = threading.Thread(target=self._read_stderr, daemon=True)
        self._out.start()
        self._err.start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        try:
            for raw in self.proc.stdout:
                if len(raw) > MAX_LINE_BYTES:
                    self.inbox.put({
                        "jsonrpc": "2.0",
                        "error": {"message": "muse frame exceeded size cap"},
                    })
                    break
                line = raw.strip()
                if not line:
                    continue
                try:
                    self.inbox.put(json.loads(line))
                except json.JSONDecodeError:
                    self.inbox.put({"jsonrpc": "2.0", "error": {"message": "non-json frame"}})
        finally:
            self.inbox.put(None)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        while True:
            chunk = self.proc.stderr.read(4096)
            if not chunk:
                break
            room = MAX_STDERR_BYTES - len(self.stderr)
            if room > 0:
                self.stderr.extend(chunk[:room])

    def send(self, frame: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        payload = (json.dumps(frame, separators=(",", ":")) + "\n").encode()
        self.proc.stdin.write(payload)
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
    allowed_root: str | None = None,
    approval_mode: str = "denyUnmatched",
    mcp_servers: Any = None,
    timeout_s: float = 600,
    contributor_mode: str = "off",
    provider: str = "meta",
    binary: str | None = None,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    env: dict[str, str] | None = None,
    api_fallback: bool = True,
    api_key: str | None = None,
    api_key_provider: Callable[[], str | None] | None = None,
) -> dict[str, Any]:
    if not isinstance(prompt, str) or not prompt.strip():
        raise MuseHarnessError("prompt is empty")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise MuseHarnessError("prompt is too large")
    if approval_mode not in APPROVAL_MODES:
        raise MuseHarnessError(f"approval_mode must be one of {', '.join(APPROVAL_MODES)}")
    bounded = workspace_within(workspace, allowed_root or workspace)
    parent = dict(env if env is not None else os.environ)
    try:
        return _one_turn(
            prompt=prompt,
            model=model,
            workspace=bounded,
            approval_mode=approval_mode,
            mcp_servers=mcp_servers,
            timeout_s=timeout_s,
            contributor_mode=contributor_mode,
            provider=provider,
            binary=binary,
            popen=popen,
            env=child_env(parent, billing="subscription"),
            billing="subscription",
        )
    except QuotaExceeded:
        if not api_fallback or provider != "meta":
            raise
        return _one_turn(
            prompt=prompt,
            model=model,
            workspace=bounded,
            approval_mode=approval_mode,
            mcp_servers=mcp_servers,
            timeout_s=timeout_s,
            contributor_mode=contributor_mode,
            provider=provider,
            binary=binary,
            popen=popen,
            env=child_env(parent, billing="api_key", api_key=_fallback_key(api_key, api_key_provider)),
            billing="api_key_fallback",
        )


def _fallback_key(
    api_key: str | None,
    provider: Callable[[], str | None] | None,
) -> str | None:
    if api_key is not None:
        return api_key
    if provider is None:
        return None
    return provider()


def _one_turn(
    *,
    prompt: str,
    model: str,
    workspace: str,
    approval_mode: str,
    mcp_servers: Any,
    timeout_s: float,
    contributor_mode: str,
    provider: str,
    binary: str | None,
    popen: Callable[..., subprocess.Popen[bytes]],
    env: dict[str, str],
    billing: str,
) -> dict[str, Any]:
    mcp = normalize_mcp_servers(mcp_servers)
    exe = binary or shutil.which("muse")
    if not exe:
        raise MuseHarnessError("muse is not on PATH. Install Muse Code, then retry.")
    proc = popen(
        serve_argv(binary=exe, model=model, provider=provider, contributor_mode=contributor_mode),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    transport = LineTransport(proc)
    text_parts: list[str] = []
    agent_items: set[str] = set()
    decisions: list[dict[str, str]] = []
    terminal = None
    error = None
    session_id = None
    next_id = 1
    deadline = time.monotonic() + timeout_s
    allow = approval_mode == "allowAll"

    def send_request(method: str, params: dict[str, Any]) -> str:
        nonlocal next_id
        req_id = str(next_id)
        next_id += 1
        transport.send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        return req_id

    def handle_server_request(frame: dict[str, Any]) -> None:
        method = frame.get("method")
        params = frame.get("params") or {}
        transport.send({"jsonrpc": "2.0", "id": frame.get("id"), "result": {}})
        if method != "approval/request":
            return
        choice = pick_choice(list(params.get("availableChoices") or []), allow=allow)
        decision = str(choice.get("decision")) if choice else "unanswered"
        decisions.append({"approvalId": str(params.get("approvalId")), "decision": decision})
        if choice is None:
            return
        send_request("approval/decide", {
            "commandId": uuid7(),
            "sessionId": params.get("sessionId") or session_id,
            "approvalId": params.get("approvalId"),
            "requirementId": params.get("currentRequirementId"),
            "choiceId": choice.get("choiceId"),
        })

    def note(frame: dict[str, Any]) -> None:
        nonlocal terminal, error
        method = frame.get("method")
        params = frame.get("params") or {}
        if method == "item/started":
            item = params.get("item") or params
            if item.get("kind") == "agentMessage" and item.get("itemId"):
                agent_items.add(str(item["itemId"]))
        elif method == "item/delta":
            item_id = params.get("itemId")
            if item_id in agent_items and params.get("field", "text") == "text":
                text_parts.append(params.get("delta") or "")
        elif method == "turn/completed":
            terminal = params.get("terminal")
            error = params.get("error")

    def pump(match_id: str | None = None, until_completed: bool = False) -> dict[str, Any] | None:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MuseHarnessError("muse turn timed out")
            frame = _next_frame(transport, remaining)
            if frame.get("error") and "method" not in frame and frame.get("id") is None:
                raise MuseHarnessError(frame["error"].get("message") or "muse request failed")
            if "method" in frame and "id" in frame and "result" not in frame and "error" not in frame:
                handle_server_request(frame)
                continue
            if match_id is not None and frame.get("id") == match_id:
                if frame.get("error"):
                    raise MuseHarnessError(frame["error"].get("message") or "muse request failed")
                return frame
            note(frame)
            if until_completed and frame.get("method") == "turn/completed":
                if is_quota_failure(frame):
                    raise QuotaExceeded((frame.get("params") or {}).get("error") or {})
                return frame

    try:
        init_id = send_request("initialize", {
            "clientInfo": {"name": "hermes_muse_harness", "version": "0.1.4"},
            "capabilities": {
                "userInputDialogs": False,
                "requestedCapabilities": ["sessionMcp"],
            },
        })
        pump(match_id=init_id)
        transport.send({"jsonrpc": "2.0", "method": "initialized"})
        start_params: dict[str, Any] = {
            "commandId": uuid7(),
            "approvalMode": approval_mode,
            "workspaceRoot": workspace,
        }
        if provider in ("meta", "local"):
            start_params["modelId"] = model
        if mcp:
            start_params["config"] = {"mcpServers": mcp}
        started = pump(match_id=send_request("session/start", start_params))
        try:
            session_id = started["result"]["session"]["sessionId"]
        except (TypeError, KeyError) as exc:
            raise MuseHarnessError("muse session/start returned no session") from exc
        turn_request = send_request("turn/start", {
            "commandId": uuid7(),
            "sessionId": session_id,
            "input": [{"type": "text", "text": prompt}],
        })
        pump(match_id=turn_request)
        pump(until_completed=True)
        return {
            "session_id": session_id,
            "model": model,
            "terminal": terminal,
            "text": "".join(text_parts),
            "approvals": decisions,
            "billing": billing,
            "error": error,
        }
    finally:
        transport.close()


def _next_frame(transport: LineTransport, timeout_s: float) -> dict[str, Any]:
    try:
        frame = transport.inbox.get(timeout=timeout_s)
    except queue.Empty as exc:
        tail = bytes(transport.stderr).decode("utf-8", "replace").strip()
        extra = f": {tail}" if tail else ""
        raise MuseHarnessError(f"muse serve stalled{extra}") from exc
    if frame is None:
        tail = bytes(transport.stderr).decode("utf-8", "replace").strip()
        extra = f": {tail}" if tail else ""
        raise MuseHarnessError(f"muse serve exited before the turn finished{extra}")
    return frame
