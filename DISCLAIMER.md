This plugin launches the Muse Code binary you already installed (`muse serve`)
and speaks Muse Session Protocol on its stdin/stdout. It does not read Muse's
credential files, does not mint a subscription key, and does not call
`api.meta.ai` itself. It is not affiliated with or endorsed by Meta.

Meta's terms say a Coding Harness Subscription credential is for the Muse Code
harness only. This plugin stays on that side of the line by leaving that
credential inside `muse`. `MUSE_CODE_SUB_TOKEN` is never forwarded.

The first attempt does not pass an API key, so the browser login is used.
Muse treats a set API key as higher priority than the login, so leaving it
set would skip the subscription. If that turn fails with MSP's typed quota
object, the same prompt is retried once with `META_API_KEY` set from Hermes
secrets (`MODEL_API_KEY`, then `META_API_KEY`, then `META_MODEL_API_KEY`),
read with `agent.secret_scope.get_secret`. That retry is pay-as-you-go. The
child process does not receive the rest of the Hermes environment.

Tool approval is `plugins.entries.muse-harness.settings.approval_mode`.
The default is `denyUnmatched`. `allowAll` lets that Muse session run its own
shell and file tools inside the session directory without asking. Hermes does
not approval-gate this plugin's tool call. The model cannot set `allowAll`;
only that setting can.
MCP servers the model passes must be loopback HTTP. Stdio commands are refused.

The authors cannot obtain, restore, or protect a Meta account.
