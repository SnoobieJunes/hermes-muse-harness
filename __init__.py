"""Hermes plugin: one Muse Code turn over MSP. Credentials stay inside muse."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

MAX_TIMEOUT_S = 1200


def _load_client():
    path = Path(__file__).resolve().parent / "client.py"
    spec = importlib.util.spec_from_file_location("muse_harness_client", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_client = _load_client()
MuseHarnessError = _client.MuseHarnessError
run_turn = _client.run_turn


def register(ctx) -> None:
    schema = {
        "name": "muse_turn",
        "description": (
            "Run one prompt inside the local Muse Code harness (muse serve / MSP). "
            "Uses the Muse Code subscription first. If Muse reports the allowance "
            "is exhausted, retries that one turn on pay-as-you-go using the Meta "
            "key from Hermes secrets (MODEL_API_KEY, then META_API_KEY, then "
            "META_MODEL_API_KEY). Does not read Muse login files. "
            "mcp_servers may only be loopback HTTP. Muse tool approval is "
            "denyUnmatched unless approval_mode is allowAll in plugin settings."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The user prompt Muse should run."},
                "model": {
                    "type": "string",
                    "description": "Muse model id. Default muse-wonder-full-1.0.",
                },
                "workspace": {
                    "type": "string",
                    "description": "Directory inside the session cwd. Defaults to the session cwd.",
                },
                "mcp_servers": {
                    "type": "string",
                    "description": (
                        "JSON object of loopback MCP servers. "
                        "Example: {\"unreal\":{\"transport\":\"streamableHttp\","
                        "\"url\":\"http://127.0.0.1:8000/mcp\"}}."
                    ),
                },
                "timeout_s": {
                    "type": "integer",
                    "description": f"Seconds to wait. Default 600, max {MAX_TIMEOUT_S}.",
                },
            },
            "required": ["prompt"],
        },
    }

    def handle(params: dict[str, Any], **kwargs: Any) -> str:
        del kwargs
        prompt = params.get("prompt") or ""
        session_cwd = _session_cwd()
        workspace = params.get("workspace") or session_cwd
        timeout = params.get("timeout_s") or 600
        try:
            timeout_s = float(timeout)
        except (TypeError, ValueError):
            return _err("timeout_s must be a number")
        if timeout_s <= 0 or timeout_s > MAX_TIMEOUT_S:
            return _err(f"timeout_s must be between 1 and {MAX_TIMEOUT_S}")
        return _execute(
            ctx,
            prompt,
            model=params.get("model"),
            workspace=workspace,
            mcp_servers=params.get("mcp_servers"),
            timeout_s=timeout_s,
        )

    def slash(raw_args: str) -> str:
        text = (raw_args or "").strip()
        if not text or text in {"help", "-h", "--help"}:
            mode = _approval(ctx)
            return (
                "/muse <prompt>\n"
                "Runs one Muse Code turn in this session directory.\n"
                f"Tool approval: {mode}."
            )
        return _execute(ctx, text)

    ctx.register_tool(
        name="muse_turn",
        toolset="muse_harness",
        schema=schema,
        handler=handle,
    )
    ctx.register_command(
        "muse",
        handler=slash,
        description="Run one prompt inside the local Muse Code harness.",
    )


def _approval(ctx) -> str:
    approval = ctx.get_config("approval_mode", None)
    if approval not in ("denyUnmatched", "allowAll"):
        return "denyUnmatched"
    return approval


def _execute(
    ctx,
    prompt: str,
    *,
    model: str | None = None,
    workspace: str | None = None,
    mcp_servers: Any = None,
    timeout_s: float = 600,
) -> str:
    session_cwd = _session_cwd()
    try:
        result = run_turn(
            prompt=prompt,
            model=model or "muse-wonder-full-1.0",
            workspace=workspace or session_cwd,
            allowed_root=session_cwd,
            approval_mode=_approval(ctx),
            mcp_servers=mcp_servers,
            timeout_s=timeout_s,
            contributor_mode="off",
            api_key_provider=_scoped_api_key,
        )
    except MuseHarnessError as exc:
        return _err(str(exc))
    except OSError as exc:
        return _err(f"failed to start muse: {exc}")
    return json.dumps({"success": True, **result})


def _scoped_api_key() -> str | None:
    try:
        from agent.secret_scope import UnscopedSecretError, get_secret
    except ImportError as exc:
        raise MuseHarnessError("Hermes secret scope is unavailable") from exc
    try:
        return _client.resolve_api_key(get_secret)
    except UnscopedSecretError as exc:
        raise MuseHarnessError("could not read the Meta API key for this profile") from exc


def _session_cwd() -> str:
    try:
        from agent.runtime_cwd import resolve_agent_cwd

        cwd = resolve_agent_cwd()
        if cwd:
            return cwd
    except Exception:
        pass
    return os.getcwd()


def _err(message: str) -> str:
    return json.dumps({"success": False, "error": message})
