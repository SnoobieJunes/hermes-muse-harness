# muse-harness

Hermes plugin that drives a local [Muse Code](https://dev.meta.ai/products/muse-code/) session over MSP (`muse serve`). Wonder Full stays inside the Muse harness. Hermes starts the turn, can attach MCP servers (Unreal, and anything else Muse should call mid-turn), and reads the result back.

This is not a model provider. It does not mint a subscription key, does not read Muse's login files, and does not call `api.meta.ai` itself. Meta's terms limit the Coding Harness Subscription credential to the Muse Code harness. Leaving that credential inside `muse` is the point.

Read [DISCLAIMER.md](DISCLAIMER.md) before installing.

## Install

From a checkout:

```bash
hermes plugins install link:/absolute/path/to/hermes-muse-harness
hermes plugins enable muse-harness
```

From GitHub, after this repo is public:

```bash
hermes plugins install SnoobieJunes/hermes-muse-harness
hermes plugins enable muse-harness
```

Requires the `muse` binary on `PATH` (`Muse Code 1.4.x`) and `muse login` already completed in a shell where `META_API_KEY` / `MODEL_API_KEY` are unset. Those variables win over the account login inside Muse, so this plugin removes them from the child process only.

## Tool

`muse_turn`

| Argument | Default | Meaning |
|---|---|---|
| `prompt` | required | The turn text. |
| `model` | `muse-wonder-full-1.0` | Model id Muse should run. |
| `workspace` | session cwd | Absolute workspace root. |
| `approval_mode` | `denyUnmatched` | `allowAll` lets Muse use its own tools without a second prompt. Hermes still approves this tool call. |
| `mcp_servers` | none | JSON object of session MCP servers. This is how Wonder Full calls Unreal during the turn. |
| `timeout_s` | 600 | Max 1200. Cron and other unattended runs fail at the timeout instead of hanging. |

Contributor mode is forced off. There is no flag to turn it on from this tool.

Example MCP value for a local Unreal server:

```json
{"unreal":{"transport":"streamableHttp","url":"http://127.0.0.1:8000/mcp"}}
```

## What this does not do

- It does not put Wonder Full in Hermes's own inference loop. Muse is the agent for that turn. Hermes tools that are not attached as MCP servers are not callable until the turn returns.
- It does not bill a Model API key. Pay-as-you-go stays on Hermes's built-in `meta-ai` provider.
- It does not log you in. Run `muse login` yourself.

## Development

```bash
python3 -m unittest discover -s tests -v
hermes plugins validate . --install-deps
```

The unit tests do not start Muse and do not need a Meta account. A live echo check is `python3 tests/live_echo.py` on a machine that has `muse`.

## Marketplace

Catalog listing is a human-merged pull request to `NousResearch/hermes-agent`, pinned to an exact commit. Publishing this repository does not by itself list the plugin. The design is the one that can be submitted: public `register_tool` surface, no credential-store reads, no client impersonation, no self-updater.
