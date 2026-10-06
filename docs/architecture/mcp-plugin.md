# A pod as a plugin in Claude and ChatGPT

A pod added to Claude or ChatGPT through [the MCP connector](mcp-connector.md)
already works: the person signs in, and the client reads and writes the pod as
them. This document is the plan for the rest — what makes a connected pod look
and behave like a plugin there rather than a list of tools that answer in JSON —
and records what is built.

Pods emitting events to MCP clients (MCP Events) is a separate piece of work and
is not planned here; [where it meets this work](#where-mcp-events-meets-this) is
noted at the end.

Written 2026-10-06, after OpenAI's DevDay. The facts about each host are from
its own documentation on that date; the sources are linked where they are used.

## The rule: the standard first, a host's extras only when detected

Claude (web, Desktop, mobile), ChatGPT and VS Code all render the open
**MCP Apps** extension (`io.modelcontextprotocol/ui`, revision 2026-01-26): a
tool names a `ui://` resource in `_meta.ui.resourceUri`, and the host draws that
HTML in a sandboxed iframe that talks JSON-RPC over `postMessage`. ChatGPT adds
`window.openai` and `_meta["openai/…"]` on top: checkout, file upload, widget
state, sidebar apps, composer mentions, plugin settings.

1. **Where the standard has a field, use it.** `_meta.ui.resourceUri`, not
   `openai/outputTemplate`; `tools/call` over the bridge, not
   `window.openai.callTool`; `ui/update-model-context`, not a ChatGPT-only
   equivalent. ChatGPT reads the standard keys too.
2. **A host extra is used only behind a feature check** (`if
   (window.openai?.requestCheckout)`), and only for something the standard
   cannot do. Nothing built so far needs one.
3. **A view never carries the answer alone.** Every result stays whole as text,
   so a host without MCP Apps (Claude Code, the Agent Host, any older client)
   loses nothing.
4. **The spec's TypeScript types are the contract, not its prose.** The
   specification's own example sends `clientInfo` in `ui/initialize`; the types,
   and every host built on the reference SDK, require `appInfo` and refuse the
   other. Views are checked against the reference host before they ship.

## Built: the table view

`lemma_pod_get_records` and `lemma_pod_query` name `ui://lemma/table`
(`app/modules/agent/services/pod_mcp_views.py`, the view itself in
`pod_mcp_table_view.html` beside it, linked from the tool's row in
`pod_mcp_tool_policy.py`).

- **What it shows.** A page of records with "1–20 of 57", a query's rows in the
  order its `SELECT` chose (with "the result was cut short" when the row cap
  applied), or one record as its fields. A table's own columns come before the
  four every table has (`id`, `created_at`, `updated_at`, `user_id`).
  Timestamps are written in the host's locale and time zone, with the stored
  value on hover. Numbers line up, including Postgres `numeric`, which arrives
  as a string so no digit is lost. A failure is shown as the failure.
- **What the person can do in it.** Page through records and sort them. Both
  call the tool again through the host, so they run through the same
  connection, scope and audit line as the model's own calls. A result that is
  all there is sorts in place; a page of a larger table sorts on the server,
  never the page alone. Expand to full screen where the host offers it.
- **What the model is told.** After the person pages or sorts, the view sends
  `ui/update-model-context` saying which rows are now on screen, with the rows
  themselves when they fit, so "the third one" means the third one shown.
- **How it looks.** The host's own style variables first (the standard names),
  Lemma's stock as the fallback; the host's light or dark theme; the host draws
  the card's edge (`prefersBorder`). Weight stays at 500.
- **What it can reach.** Nothing. The declared CSP is empty, every row is drawn
  as text (a test refuses `innerHTML`), and only messages from the host frame
  are listened to.
- **Why the link is unconditional.** The server is stateless, so a client's
  `initialize` capabilities are gone by the time it lists tools. Hosts that do
  not render MCP Apps ignore the key.

Verified against the MCP Apps reference host (`examples/basic-host` in
[ext-apps](https://github.com/modelcontextprotocol/ext-apps)): its sandbox
proxy, CSP and bridge, reaching a local API through the public `/mcp/{pod}`
mount with a real `pod:read` OAuth token. That run caught the `appInfo` mistake
above. **Not yet verified in claude.ai or ChatGPT**, which connect from their own
servers and need a publicly reachable API.

## What comes next, in order

| # | Piece | Size | Needs |
|---|---|---|---|
| 1 | Table view | built | — |
| 2 | Open a row; "Open in Lemma" | small | a stable in-app URL for a table, a record and a file |
| 3 | `search` and `fetch` for ChatGPT company knowledge | small | the same URLs, for citations |
| 4 | Waiting-form and approval card | medium | two new pod tools, `pod:write` |
| 5 | Claude Code plugin in a marketplace repository | small | MCP-first skill text; Deepak's go-ahead to publish |
| 6 | Directory listings | — | the decision in [§d](#d-directory-listings-and-one-url-per-pod), submission assets |
| 7 | A pod's own app as the view | large | an SDK bridge transport, an app-only request tool, apps as resources — [§b](#b-a-pods-own-app-inside-the-host); spike first |

### a. More views

**Record detail.** Partly built: a single-record result already renders as its
fields inside the table view. The next step is in the same view — a row opens
its record by calling `pod_get_records` with its id — not a second resource. A
separate `ui://lemma/record` earns its place only if it edits, which means an
app-only write tool (`_meta.ui.visibility: ["app"]`, hidden from the model) and
`pod:write`; leave that until someone asks to edit from inside a chat.

**"Open in Lemma".** Until an app can run as the view (§b), this is how a person gets from a chat to it. `ui/open-link` is standard and both hosts implement it
(Claude always asks the person to confirm for a custom connector; a directory
listing can allowlist its destinations). It needs a stable
in-app URL for a table, a record and a file, passed in the result's `_meta`,
which the host gives the view but not the model.

**Waiting-form and approval card.** A workflow run waiting on a form, or an
agent waiting on an approval, is the case where a person most wants to act
without opening Lemma. The pieces exist (`lemma workflows runs waiting`, the
run's `submit-form` API with the form's schema), but the pod toolset served over
MCP has neither, so this starts with two tools: one that lists what is waiting
on the person (read) and one that submits a form (write, destructive, audited).
The card renders the form from its schema and submits through `tools/call`.
ChatGPT's "rich forms" are a different mechanism: they extend MCP form
elicitation, where the server asks in the middle of a call, and a listed plugin
must do that with MCP 2026-07-28's multi-round-trip requests
([OpenAI MCP extensions](https://github.com/openai/mcp-extensions/blob/main/docs/spec.md#openai-form-elicitation)).
The card comes first because it works in both hosts today and needs no session
on a stateless server; elicitation is the follow-up.

### b. A pod's own app inside the host

There are two ways to put a pod app in a conversation. Framing the deployed app
does not work. Running the app's own files as the view, with the SDK talking
through the host, can work; it is a real project, so it waits behind the
smaller pieces above.

**Framing the deployed app: no.**

- **MCP Apps has no "load this URL" mode.** A view is HTML the server returns;
  the `externalUrl` content type is deferred in the spec and still future work
  in its draft. A hosted page can only be a nested iframe from an origin listed
  in `_meta.ui.csp.frameDomains`.
- **Claude restricts `frameDomains`** "pending security review"
  ([Claude MCP Apps design guidelines](https://claude.com/docs/connectors/building/mcp-apps/design-guidelines)).
- **ChatGPT allows it only for the MCP server's own registrable domain**, with a
  justification at review
  ([ChatGPT UI guide](https://developers.openai.com/plugins/build/chatgpt-ui)),
  which a self-hosted deployment's app domain need not satisfy.
- **The app could not sign in.** The SDK authenticates with the Lemma session
  cookie, and a private app is opened with the `__Host-lemmaAppAccess` cookie
  (#860). Both are `SameSite=Lax`, which no browser sends from a frame nested
  in another site's page, so the app would load and every data call would fail
  — in every browser, not only Safari. Making them `SameSite=None; Partitioned`
  would still leave a partitioned cookie that never sees the person's Lemma
  session, Safari dropping such cookies across some redirect chains (WebKit
  bug 306194), and a nested frame inheriting sandbox flags that neither host
  documents.

**Running the app as the view: yes, with three pieces.** A deployed app is
static files (a Vite build, or one `index.html`) in object storage, and its
JavaScript reaches Lemma only through the SDK. So the server can return the
app's own `index.html` as a `ui://` resource, and the SDK can make its calls
through the host instead of over HTTP. The connection's grant is then the
app's authority: the person, under their roles and row-level security, within
`pod:read` or `pod:write`. That is what the app has inside Lemma, with no cookie,
no `frameDomains` and nothing for either host to approve beyond the view.

1. **A bridge transport in the SDK.** Today `LemmaClient` calls the global
   `fetch` and a global generated-client singleton, with no transport option
   (`lemma-typescript/src/http.ts`, `generated.ts`). It needs one, and an
   implementation that sends each request as a `tools/call` through the host.
   `window.__LEMMA_CONFIG__`, which the server already injects into an app's
   entrypoint, can select it.
2. **An app-only tool that runs those requests.** Hidden from the model
   (`_meta.ui.visibility: ["app"]`), limited to routes under this pod, with
   reads needing `pod:read` and anything else `pod:write`, and the same audit
   line as every other call. This is broader than the curated pod toolset — it
   is the app's whole API surface — which is the security decision to make
   deliberately.
3. **Apps as per-pod resources.** `ui://lemma/apps/<slug>`, listed per pod the
   way tools are, built from the app's current release with its scripts,
   styles and small assets inlined (a private app's assets are cookie-gated, so
   they cannot be fetched from a `resourceDomains` origin). A tool to open one
   carries its `resourceUri`, and asks for full screen; on ChatGPT, behind a
   feature check, `openai/ui` can also make it a sidebar app.

What does not carry over, and needs a fallback in the SDK:

- **Push.** `useLiveRecords` rides a WebSocket and agent replies stream over
  SSE. A view hears only what its host forwards, so these become refetches
  and whole replies.
- **History routing** inside the host's frame; hash routing is the safe
  choice.
- **File upload.** The standard has none; ChatGPT's `uploadFile` behind a
  feature check is the only option.
- **Connector sign-in popups.**

The same transport would let Lemma widgets render in Claude and ChatGPT too,
since a widget is HTML that reaches Lemma through the same SDK.

The first step is a spike: one no-build app (a single `index.html`, with the SDK
inlined) running through the bridge in the reference host, before committing to
the SDK change.

### c. Packaging Lemma as one plugin

**Claude Code.** One `lemma` plugin in a public marketplace repository
(`.claude-plugin/marketplace.json`). Its `plugin.json` declares a required
`userConfig.pod_url` (`type: "string"`), and its `.mcp.json` is `{"type":
"http", "url": "${user_config.pod_url}"}` — Claude Code substitutes `user_config`
in an HTTP server's `url`
([plugins reference](https://code.claude.com/docs/en/plugins-reference)). The
skills go in `skills/<name>/SKILL.md`, as `lemma-skills/` already is. Two
things to settle first:

- `lemma-user` and `lemma-data-analysis` are written for the `lemma` CLI. In a
  plugin whose only reach into the pod is the connector, they need an MCP-first
  path ("use the `lemma_pod_*` tools; the CLI if it is installed").
- Publishing the repository is outward-facing: Deepak's call, and not done here.

Claude Code draws no MCP Apps, so the views do not matter there.

**ChatGPT.** A ChatGPT plugin is an MCP server plus skills plus UI, in a
portable layout (`plugin.json` against the agent-plugins.org schema,
`skills/`, `mcp.json`). ChatGPT does not expand `user_config`, so a package
cannot carry a per-person pod URL; without a directory listing, a person adds
the pod by URL as a custom MCP server, which works today, and gets the tools
and views but no skills. A server can also offer skills itself
(`io.modelcontextprotocol/skills`, `skills/list`), but ChatGPT reads them only
at submission, as a static snapshot, and the extension is a draft (SEP-2640)
rather than part of the specification
([ChatGPT MCP server guide](https://developers.openai.com/plugins/build/mcp-server)).
It is a packaging route for a listed plugin, not a way for a pod's connector to
carry its own skills.

**Submitting, when it comes to that.** Both directories need a reviewer test
account with sample data and no emailed codes or magic links, documentation and
privacy URLs, and a title plus `readOnlyHint`/`destructiveHint` on every tool
(the last already holds). ChatGPT adds business verification, a domain check at
`/.well-known/openai-apps-challenge`, terms and support URLs, five positive and
three negative test cases, a demo video, a CSP matching what the UI does
(declared, and empty), and `_meta.ui.domain`, unique per plugin; projects with
EU data residency cannot submit. Claude adds three to five screenshots of the
MCP Apps. Claude's `ui.domain` is derived from the connector URL (a hash of it
under `claudemcpcontent.com`), so a per-pod URL means a per-pod domain, and the
resource's `_meta` would have to be built per request rather than once. Leaving
`domain` out, as now, gives each conversation its own sandbox origin, which is
fine until something needs a stable one.

**Company knowledge (`search` and `fetch`).** Worth adding, and small. ChatGPT
uses a connector as a deep-research and company-knowledge source when it has
read-only tools named `search` (one query string in; `results` of `{id, title,
url}` out) and `fetch` (`id` in; `{id, title, text, url, metadata}` out), each
returned as `structuredContent` and as the same JSON in text
([ChatGPT MCP guide](https://developers.openai.com/api/docs/mcp)). They map onto
`pod_search_files` and `pod_read_file`. A citation appears only when `url` is
set, so they want the in-app URLs from §a first.

### d. Directory listings and one URL per pod

The recorded decision is [one URL per pod](mcp-connector.md#decisions): the URL
is the RFC 8707 resource, the token's audience is that pod, and a token for one
pod is refused by every other. A directory listing usually wants one URL.

| Option | What changes | Cost |
|---|---|---|
| **1. Keep per-pod URLs; Claude lists them by pattern** | Nothing on the server. Claude's directory accepts a URL pattern (an anchored regex, each person entering their own URL) with CIMD or dynamic registration ([submission guide](https://claude.com/docs/connectors/building/submission)) | ChatGPT has no self-serve equivalent: its "template" URLs are for partners it already trusts, and are filled in per workspace by an admin |
| **2. One org-level URL, the pod picked at consent** | The resource becomes the organization's; the grant stores the pod chosen on the consent screen | The pod becomes a fact stored beside the token rather than one the client asked for — what the decision avoided. A client holds one connection per URL, so a person gets one pod per ChatGPT account, and changing pods means reconnecting |
| **3. One account-level URL serving every pod the person allows** | The consent screen lists pods to allow; the grant holds that set; every tool takes a `pod` argument, checked against the grant on every call; the audit line names the pod | Changes the decision and every tool's schema; the model can now act in the wrong pod, so the consent screen and the tool descriptions must make the pod unmistakable |
| **4. Both** | Per-pod URLs as they are, for custom connectors and Claude's pattern listing, plus option 3 at a second endpoint for ChatGPT's directory | Two resources to keep in step |

**Recommendation.** Change nothing for Claude: option 1 lists Lemma with no
server change. For ChatGPT, whether a directory listing is worth option 3 —
behind a second endpoint, so the per-pod URLs keep their guarantee (option 4) —
is a product decision for Deepak. Option 2 is the weakest of the three that
change anything: it gives up the audience guarantee and caps a person at one
pod. Nothing here has been changed.

## Where MCP Events meets this

Out of scope here; two interfaces to keep. A view refreshes itself by calling
its own tool again with the arguments it holds, so an event that says "this
table changed" needs only to reach that path. And a view hears only what its
host forwards: it cannot subscribe to the server, so any refresh an event
triggers goes through the host.
