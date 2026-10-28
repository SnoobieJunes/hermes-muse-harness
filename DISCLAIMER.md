This plugin launches the Muse Code binary you already installed (`muse serve`)
and speaks Muse Session Protocol on its stdin/stdout. It does not read Muse's
credential files, does not mint a subscription key, and does not call
`api.meta.ai` itself.

Meta's terms say a Coding Harness Subscription credential is for the Muse Code
harness only. This plugin stays on that side of the line by leaving the
credential inside `muse`. A Model API key in the environment is removed from
the child process so it cannot silently replace the subscription login.

`approval_mode=allowAll` lets Muse run its own tools without a second prompt.
That is off unless the tool call sets it. Hermes still has to approve the
tool call itself.

The authors cannot obtain, restore, or protect a Meta account.
