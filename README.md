# muse-harness

Hermes plugin that drives a local [Muse Code](https://dev.meta.ai/products/muse-code/) session over MSP (`muse serve`). Wonder Full stays inside the Muse harness. Hermes starts the turn, can attach a loopback MCP server (Unreal), and reads the result back.

This is not a model provider. It does not mint a subscription key, does not read Muse's login files, and does not call `api.meta.ai` itself.

Read [DISCLAIMER.md](DISCLAIMER.md) before installing.

## Billing

Subscription first. The first attempt does not pass an API key, so Muse uses the browser login. If the turn fails with MSP's typed `error.quota` object, the same prompt is retried once on pay-as-you-go. The key comes from Hermes secrets via `agent.secret_scope.get_secret`, in order: `MODEL_API_KEY`, `META_API_KEY`, `META_MODEL_API_KEY`. It is not read from the process environment. The child process receives an allowlist (`PATH`, `HOME`, `USER`, `LANG`, `LC_*`, `TMPDIR`, `XDG_*`) plus `META_API_KEY` on that retry only. The result field `billing` is `subscription` or `api_key_fallback`.

`MUSE_CODE_SUB_TOKEN` is never forwarded.

## Install

```bash
hermes plugins install SnoobieJunes/hermes-muse-harness
hermes plugins enable muse-harness
```

Requires `muse` on `PATH` and `muse login` already completed. Tool approval comes from `plugins.entries.muse-harness.settings.approval_mode`.

## Slash command

`/muse <prompt>` runs the same turn. `/muse` with no prompt prints the current approval mode.

## Tool

`muse_turn`

| Argument | Default | Meaning |
|---|---|---|
| `prompt` | required | The turn text. |
| `model` | `muse-wonder-full-1.0` | Model id Muse should run. |
| `workspace` | session cwd | Must stay inside the session directory. |
| `mcp_servers` | none | Loopback HTTP only. Example below. |
| `timeout_s` | 600 | Max 1200. |

```json
{"unreal":{"transport":"streamableHttp","url":"http://127.0.0.1:8000/mcp"}}
```

Contributor mode is forced off.

## What this does not do

- It does not put Wonder Full in Hermes's own inference loop.
- It does not accept a model-supplied shell command as an MCP server.
- It does not log you in. Run `muse login` yourself.

## Development

```bash
python3 -m unittest discover -s tests -v
hermes plugins validate . --install-deps
```

A live echo check is `python3 tests/live_echo.py` on a machine that has `muse`.
