This plugin launches the Muse Code binary you already installed (`muse serve`)
and speaks Muse Session Protocol on its stdin/stdout. It does not read Muse's
credential files, does not mint a subscription key, and does not call
`api.meta.ai` itself.

Meta's terms say a Coding Harness Subscription credential is for the Muse Code
harness only. This plugin stays on that side of the line by leaving that
credential inside `muse`. `MUSE_CODE_SUB_TOKEN` is never forwarded.

The first attempt drops `META_API_KEY` and `MODEL_API_KEY` from the child so
the browser login is used. Muse treats a set API key as higher priority than
the login, so leaving it set would skip the subscription. If that turn fails
with MSP's typed quota object, the same prompt is retried once with
`META_API_KEY` set from the parent environment. That retry is pay-as-you-go.

`MUSE_HARNESS_APPROVAL=allowAll` lets Muse run its own tools without a second
prompt. The model cannot set that. Hermes still has to approve the tool call.
MCP servers the model passes must be loopback HTTP. Stdio commands are refused.

The authors cannot obtain, restore, or protect a Meta account.
