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
| 2 | Open a row; a file view; "Open in Lemma" | small | a stable in-app URL for a table, a record and a file |
| 3 | `search` and `fetch` for ChatGPT company knowledge | small | the same URLs, for citations |
| 4 | Waiting-form and approval card | medium | two new pod tools, `pod:write` |
| 5 | Claude Code plugin in a marketplace repository | small | MCP-first skill text; Deepak's go-ahead to publish |
| 6 | Directory listings | — | the decision in [§d](#d-directory-listings-and-one-url-per-pod), submission assets |
| 7 | A pod's own app, framed, in ChatGPT then Claude | built | Claude to lift its `frameDomains` restriction — [§b](#b-a-pods-own-app-inside-the-host) |

### a. More views

**Record detail.** Partly built: a single-record result already renders as its
fields inside the table view. The next step is in the same view — a row opens
its record by calling `pod_get_records` with its id — not a second resource. A
separate `ui://lemma/record` earns its place only if it edits, which means an
app-only write tool (`_meta.ui.visibility: ["app"]`, hidden from the model) and
`pod:write`; leave that until someone asks to edit from inside a chat.

**A file view.** `pod_read_file`, `pod_list_files` and
`pod_view_document_pages` answer with text and page images; a view can show a
folder as a list and a document as its pages, the same way the table view shows
records.

**"Open in Lemma".** `ui/open-link` is standard and both hosts implement it
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

Views are for what Lemma itself shows — tables, records, files, forms. A pod's
app is shown as itself: the deployed app, framed inside a thin Lemma view and
signed in with a token the connection mints, not rebuilt as a view.

**Why it is framed, and what the frame needs.**

- **MCP Apps has no "load this URL" mode.** A view is HTML the server returns;
  the `externalUrl` content type is deferred in the spec. A deployed app can
  only be a nested iframe from an origin the view lists in
  `_meta.ui.csp.frameDomains`.
- **ChatGPT allows it for the MCP server's own registrable domain, and for a
  third-party domain where the embedded experience is essential.** Either way a
  listed plugin justifies each iframe at submission — what it shows, why, and
  who controls its domain — and iframe use can mean extra review or escalation;
  sharing the server's domain does not guarantee approval
  ([plugin guidelines](https://developers.openai.com/plugins/plugin-guidelines)).
  **Claude restricts `frameDomains`** "pending security review"
  ([Claude MCP Apps design guidelines](https://claude.com/docs/connectors/building/mcp-apps/design-guidelines)),
  so ChatGPT comes first and Claude follows when it opens.
- **Not with cookies.** The SDK's session cookie and the #860
  `__Host-lemmaAppAccess` cookie are both `SameSite=Lax`, which no browser sends
  from a frame inside another site's page. The SDK already has a token mode —
  HTTP calls carry it as `Authorization: Bearer`, and the datastore-changes
  WebSocket, which a browser cannot give headers, as an `access_token` query
  parameter — so the framed app signs in with a token instead.

**Built.** Each app the person may open is a tool of its own and a view of
its own (`app/modules/agent/services/pod_mcp_apps.py`, the view in
`pod_mcp_app_view.html` beside it):

- **`lemma_open_app_<slug>`**, titled "Open ⟨app⟩" and described from the app's
  own description, for the model to pick and ChatGPT's suggestions to rank. It
  names `ui://lemma/apps/<slug>`, whose CSP lets the view frame exactly that
  app's host (`frameDomains`) and reach nothing else. Listed for the pod's
  ready apps the person may read, on a deployment that serves app hosts, up to
  twenty.
- **`lemma_app_session`**, which only a view can call (`visibility: ["app"]`),
  mints the framed app's token: a delegated token for the pod's own agent,
  `session_id` `mcp:<connection>`, no actor name (any other name demotes it to
  a named workload with only its own grants). It is the person, in this pod:
  their roles and row-level security, refused for any other pod, destructive
  actions gated. It travels in the result's `_meta`, which hosts give the view
  and keep from the model; ChatGPT shows the model `structuredContent` too.
- **`lemma_app_access`**, also view-only, mints a private app's one-minute
  ticket from the connection instead of a Lemma session, bound to the person,
  the app's origin, a fresh delegated session and the connection. Redeemed with
  `embedded: true`, it sets a `SameSite=None; Partitioned` cookie — sent from
  the frame, kept to that embedding. Only a connection's ticket earns that
  cookie, and a connection's ticket earns no other.

The SDK (`lemma-typescript/src/embedded.ts`) finds itself framed by the
`lemma_embed=mcp` mark the view adds to the app's address, remembers it for the
frame's life, and asks the view for a token before its first request, again a
minute before the token expires, and once more on a 401. The private app's
sign-in page asks the view for its ticket the same way. The view answers only
the frame it made, and only at that app's origin.

**What it deliberately does not do.**

- **A read-only connection is shown no apps.** The token a framed app holds is
  not narrowed by the connection's scopes — the `scope` claim is enforced only
  for named workloads (`workload_authority.py`), and widening that would also
  change how the pod's agent runs functions — so apps are offered only where
  the person allowed changes. A read-only connection still has every view.
- **Compose is not offered.** An app's "ask in the conversation"
  (`composeInConversation`) fills the composer and never sends. Neither host
  documents a way to offer text without sending it (`ui/message` sends;
  ChatGPT's `openai/message` only adds a target), so the view does not answer
  and the SDK reports the offer as not taken.
- **`GET /organizations` answers** for the token, listing the person's own
  organizations. Organization-level actions are refused. The same app opened
  in Lemma holds the person's whole session, so this is no wider than what it
  already sees; left as it is.

**How access ends.** A framed private app's cookie is re-checked at most every
`app_access_cache_ttl_seconds`, and the check now asks whether the connection
still stands, so revoking it, a replay ending it, the pod's deletion or its age
ceiling all end the app's files within that window. The API token is an
ordinary access token and lasts until it expires (the core's access-token
lifetime); every new one is minted only for a live connection.

**Apps built before this SDK.** A no-build app loads the SDK from
`/public/sdk/lemma-client.js` and is framed-ready as soon as this deploys. A Vite
app bundles the SDK it was built with and needs a rebuild to ask the view for
its token.

**Probed in ChatGPT first, 2026-10-06.** A throwaway branch framed a real pod
app in ChatGPT's desktop app through a developer-mode connector before any of
this was written. ChatGPT framed both the MCP server's own host and another
site (the same-registrable-domain rule is for review, not enforced in
developer mode); the frame had its own origin, `localStorage`, IndexedDB, and
kept `SameSite=None` cookies; the delegated token read and wrote records, was
refused for another pod, opened the datastore WebSocket and drove the SDK.
ChatGPT's host offered `serverTools`, `openLinks`, `updateModelContext`,
`message`, inline and fullscreen. Claude still restricts `frameDomains`.

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
