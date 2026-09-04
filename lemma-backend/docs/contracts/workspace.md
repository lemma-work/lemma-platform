# workspace contract

What every `workspace` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `workspace.browser.access` | POST | `/workspace/apps/browser/access` | Create workspace browser access URL |
| `workspace.browser.heartbeat` | POST | `/workspace/apps/browser/heartbeat` | Keep the workspace browser awake while somebody is watching |
| `workspace.browser.targets` | GET | `/workspace/apps/browser/targets` | List pages the workspace browser has open |
| `workspace.files.content` | GET | `/workspace/files:content` | Read workspace file content |
| `workspace.files.list` | GET | `/workspace/files` | List workspace files |
| `workspace.files.stat` | GET | `/workspace/files:stat` | Stat one workspace file |
| `workspace.takeover.create` | POST | `/workspace/takeover` | Ask a person to drive the workspace browser |
| `workspace.takeover.heartbeat` | POST | `/workspace/takeover/{request_id}:heartbeat` | Keep the browser alive while somebody is typing |
| `workspace.takeover.open` | GET | `/workspace/takeover/{request_id}` | Open a takeover and get the live browser URL |
| `workspace.takeover.resolve` | POST | `/workspace/takeover/{request_id}:resolve` | Say the takeover is finished |

<!-- /generated:operations -->

## `workspace.files.list`

Lists one directory of the caller's own workspace. A workspace is keyed by user,
so the session is the whole authorization check and there is no sandbox
parameter to point elsewhere.

**Ambient by default.** A paused workspace is not started to answer; the response
comes back with `sleeping: true` and no entries. `wake=true` starts it. This is
deliberate: idle release is 900 seconds, so a file pane that started a sandbox on
every render would hold compute open for as long as the pane was on screen.

Paths resolve under `/workspace` and refuse anything outside it, `/tmp`
included — that is where `github_credential_bridge` stages a credential, and an
HTTP route able to read it would publish it to any request carrying the caller's
session.

Returns at most 1000 entries with `truncated: true` when there are more.

## `workspace.files.stat`

One entry's kind, size and modification time. Same path rules as the listing.
Unlike the listing this always starts the workspace, because a caller asking
about one named path wants an answer rather than a "not now".

## `workspace.files.content`

Streams a byte range of one file, defaulting to the first 8 MB. Served as
`application/octet-stream` with `Content-Disposition: attachment` and
`X-Content-Type-Options: nosniff`, because workspace files are the person's own
content and must never render as markup on the API origin.

Refuses with 404 for a path that is not there, 413 for a file larger than the
endpoint serves in one request, and 503 when the workspace cannot be reached.

## `workspace.takeover.create`

Records that the agent needs a person to drive the browser: which site, which
conversation, and why. Returns the request id that addresses it.

## `workspace.takeover.open`

Returns the request together with a freshly signed URL for the live browser
view. **The id is a lookup, not a credential** — every read is checked against
the caller's own session, because this link travels through Slack and WhatsApp,
whose unfurl bots fetch every URL they are shown. A request belonging to somebody
else is reported as missing rather than forbidden, so the response cannot confirm
an id to a holder who should not have one.

Refuses with 503 when no port-access signing key is configured, because there is
then no URL to hand back.

## `workspace.takeover.heartbeat`

Touches the browser so it is still there when the person finishes typing.
`agent-browser` closes Chrome after two minutes without a command and the sandbox
releases after fifteen minutes idle — both shorter than finding a password and
getting through a second factor. One trivial command through the workspace
session resets both clocks.

## `workspace.takeover.resolve`

Closes the request as done or cancelled, which is what unblocks whatever asked
for it. Keeps the remaining TTL rather than extending it: resolving is the end of
the exchange, not a reason to hold the record open longer.

## `workspace.browser.heartbeat`

Keeps the workspace browser awake while somebody is watching it.

Watching is not a command, and `agent-browser` closes Chrome after two minutes
without one — so a live view with nobody typing goes dark on its own, which
reads as a crash rather than a timeout. One trivial command resets both clocks:
the daemon's idle timer, and the sandbox's own activity clock.

Shared with `workspace.takeover.heartbeat` rather than duplicated, so the two
cannot drift on what keeping a browser alive means.

## `workspace.browser.targets`

The pages the workspace browser has open, so a viewer knows what there is to
watch. Empty rather than an error when the browser is not running: a workspace
whose browser has been shed for idleness or memory is the ordinary resting
state.

## The browser stream (websocket)

`WS /workspace/apps/browser/stream` carries one page's debugging protocol to a
viewer, which is what makes the browser *drivable* rather than merely visible —
the dashboard `agent-browser` ships has no input path at all.

It authenticates its own handshake (bearer, session cookie, or `access_token`
query parameter) because the global HTTP auth dependency cannot see an upgrade,
the same arrangement the datastore changes socket uses. A workspace is keyed by
user, so the session is the whole authorization check.

**The protocol is filtered in the sandbox, not here.** `Input.*` and the four
screencast methods pass; everything else is refused, `Page.navigate` included.
Raw CDP reads every cookie and evaluates arbitrary script, so a page holding
this socket must not be able to reach it — see `cdp_message_is_allowed` in the
runtime. Nothing on this path logs a message body: input frames carry what
somebody is typing, and here that is a password.

Closes with 4401 when there is no session, and 4409 when there is no browser to
attach to — distinguishable so the viewer can say which.
