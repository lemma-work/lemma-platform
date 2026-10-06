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
| — | A pod's own app inside the host | not now | see [§b](#b-a-pods-own-app-inside-the-host) |

### a. More views

**Record detail.** Partly built: a single-record result already renders as its
fields inside the table view. The next step is in the same view — a row opens
its record by calling `pod_get_records` with its id — not a second resource. A
separate `ui://lemma/record` earns its place only if it edits, which means an
app-only write tool (`_meta.ui.visibility: ["app"]`, hidden from the model) and
`pod:write`; leave that until someone asks to edit from inside a chat.

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

Not feasible to do well today; do not build it yet.

- **MCP Apps has no "load this URL" mode.** A view is HTML the server returns;
  the `externalUrl` content type is deferred in the spec and still future work
  in its draft. The only way to show a hosted app is a nested iframe from an
  origin listed in `_meta.ui.csp.frameDomains`.
- **Claude restricts `frameDomains`** "pending security review"
  ([Claude MCP Apps design guidelines](https://claude.com/docs/connectors/building/mcp-apps/design-guidelines)).
  Blocked there.
- **ChatGPT allows it only for the MCP server's own registrable domain**, with a
  justification at review
  ([ChatGPT UI guide](https://developers.openai.com/plugins/build/chatgpt-ui)).
  The app would have to be served from the API's registrable domain, which a
  self-hosted deployment need not do.
- **The #860 cookie would not survive.** Inside a chatgpt.com frame the app's
  cookie is third-party: Safari blocks it by default, and Chrome only while the
  person allows third-party cookies. It would have to be `SameSite=None;
  Partitioned` (CHIPS), set by redeeming the one-minute ticket *inside* the
  frame, and it would never see the person's first-party Lemma session. Safari
  also drops partitioned cookies across some redirect chains (WebKit bug
  306194). And a nested frame inherits the view's sandbox flags, which neither
  host documents; without `allow-same-origin` there are no cookies at all.

So the app stays in Lemma, reached by "Open in Lemma" (§a), and what an app
shows that matters in a chat is drawn by Lemma's own views — table, record,
form. Revisit when the spec ships `externalUrl` or Claude lifts the
`frameDomains` restriction.

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
