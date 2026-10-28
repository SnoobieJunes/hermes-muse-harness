"""Hermes plugin: one Muse Code turn over MSP, credential left inside muse."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_PLUGIN_DIR = str(Path(__file__).resolve().parent)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from client import MuseHarnessError, run_turn

MAX_TIMEOUT_S = 1200


def register(ctx) -> None:
    schema = {
        "name": "muse_turn",
        "description": (
            "Run one prompt inside the local Muse Code harness (muse serve / MSP). "
            "Use this for Muse Wonder Full and for any turn that must spend the "
            "Muse Code subscription. Muse keeps its own login; this tool never "
            "reads it. Pass mcp_servers to let that session call Unreal or other "
            "MCP tools during the turn. approval_mode defaults to denyUnmatched; "
            "set allowAll only when Muse must run its own tools without a second prompt."
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
                    "description": "Absolute workspace root. Defaults to the session cwd.",
                },
                "approval_mode": {
                    "type": "string",
                    "enum": ["denyUnmatched", "onRequest", "allowAll"],
                    "description": "Muse approval mode. Default denyUnmatched. allowAll is explicit.",
                },
                "mcp_servers": {
                    "type": "string",
                    "description": (
                        "JSON object of MCP servers for this session only. "
                        "stdio: {\"transport\":\"stdio\",\"command\":\"bin\",\"args\":[]}. "
                        "http: {\"transport\":\"streamableHttp\",\"url\":\"http://127.0.0.1:8000/mcp\"}."
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
        workspace = params.get("workspace") or _session_cwd()
        approval = params.get("approval_mode") or "denyUnmatched"
        timeout = params.get("timeout_s") or 600
        try:
            timeout_s = float(timeout)
        except (TypeError, ValueError):
            return _err("timeout_s must be a number")
        if timeout_s <= 0 or timeout_s > MAX_TIMEOUT_S:
            return _err(f"timeout_s must be between 1 and {MAX_TIMEOUT_S}")
        try:
            result = run_turn(
                prompt=prompt,
                model=params.get("model") or "muse-wonder-full-1.0",
                workspace=workspace,
                approval_mode=approval,
                mcp_servers=params.get("mcp_servers"),
                timeout_s=timeout_s,
                contributor_mode="off",
            )
        except MuseHarnessError as exc:
            return _err(str(exc))
        except OSError as exc:
            return _err(f"failed to start muse: {exc}")
        return json.dumps({"success": True, **result})

    ctx.register_tool(
        name="muse_turn",
        toolset="muse_harness",
        schema=schema,
        handler=handle,
    )


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
