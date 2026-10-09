# Pods as remote MCP servers

A person can add one of their pods to Claude (claude.ai, Desktop, Claude Code),
ChatGPT or any other MCP client by URL, sign in to Lemma, and the client then
works in that pod as them: reading and writing its tables and files under the
same permissions and row-level security they have in Lemma.

```
https://<api>/mcp/<pod id>
```

The code is `lemma-backend/app/modules/mcp_access/` (the authorization server,
consent and grants) and `lemma-backend/app/mcp_server.py` (the endpoint). The
consent page is `lemma-frontend/src/auth/mcp-consent-screen.tsx`.

## What the clients require

Checked against each vendor's documentation at the time of writing. The MCP
revision is 2026-07-28.

| Client | How it registers | Redirect URI | What else it needs |
|---|---|---|---|
| Claude (claude.ai, Desktop, mobile) | A client ID metadata document when the server advertises both `client_id_metadata_document_supported` and `"none"` among token endpoint auth methods; dynamic registration otherwise; or a client ID pasted under Advanced settings | `https://claude.ai/api/mcp/auth_callback` | Starts sign-in **only** on a 401 carrying `resource_metadata`. Uses the first authorization server only. The resource in the metadata must equal the URL the person entered. Reaches the server from `160.79.104.0/21`, so the server must be public. Requires `readOnlyHint`/`destructiveHint` on every tool |
| Claude Code | The same metadata document (`https://claude.ai/oauth/claude-code-client-metadata`), dynamic registration, or `--client-id` | `http://localhost:<any port>/callback` | `claude mcp add --transport http <name> <url>`, then `/mcp` or `claude mcp login <name>` |
| ChatGPT | A metadata document (`https://chatgpt.com/oauth/...`) preferred; dynamic registration; pre-registered | `https://chatgpt.com/connector_platform_oauth_redirect` when the server returns `iss`; `https://chatgpt.com/connector/oauth/{callback_id}` otherwise | Token audience equal to the `resource` it sent. `search`/`fetch` tools are needed only to be a "company knowledge" source, not to connect. Tools without `readOnlyHint` are treated as writes and confirmed |
| Any 2026-07-28 client | Pre-registered, then metadata document, then dynamic registration (now deprecated but still the fallback) | Its own | Refuses a server whose metadata lacks `code_challenge_methods_supported`. Sends `resource` on both authorize and token requests. Validates RFC 9207 `iss` |

Sources:
[MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization),
[client registration](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration),
[streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http),
[tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools),
[Claude connector authentication](https://claude.com/docs/connectors/building/authentication),
[adding a custom connector to Claude](https://claude.com/docs/connectors/custom/add-unlisted),
[Claude Code MCP](https://code.claude.com/docs/en/mcp),
[ChatGPT plugin auth](https://developers.openai.com/plugins/build/auth),
[ChatGPT developer mode](https://developers.openai.com/api/docs/guides/developer-mode).

### What an interactive UI (MCP Apps) needs

A tool names a `ui://` resource in `_meta.ui.resourceUri`; the resource is HTML
served with MIME type `text/html;profile=mcp-app`, rendered by the host in a
sandboxed iframe that talks JSON-RPC over `postMessage` (`ui/initialize`, then
`ui/notifications/tool-result` carrying the result). Claude, Claude Desktop,
ChatGPT, VS Code and others render them; ChatGPT also reads its older
`openai/outputTemplate` key as an alias. Sources:
[MCP Apps overview](https://modelcontextprotocol.io/extensions/apps/overview),
[the specification](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx),
[client support](https://modelcontextprotocol.io/extensions/client-matrix),
[MCP Apps in ChatGPT](https://developers.openai.com/apps-sdk/mcp-apps-in-chatgpt).

Record listings and query results render as a table (`ui://lemma/table`); what
it does, how it was verified, and what comes next are in
[A pod as a plugin](mcp-plugin.md).

## Decisions

**An authorization server of our own, on the SDK's handlers.** Three options:

- *SuperTokens' OAuth2 provider recipe.* Available only on SuperTokens' managed
  service, not self-hosted
  ([docs](https://supertokens.com/docs/authentication/unified-login/introduction)),
  which a self-hosted Lemma cannot assume. It also has no dynamic registration
  or metadata documents, and its discovery document omits
  `code_challenge_methods_supported`, which spec-following clients refuse.
- *fastmcp's OAuth proxy.* It proxies an upstream OAuth provider. Lemma's
  identity provider is SuperTokens sessions, which is not one.
- *Our own.* The MCP SDK already implements the HTTP half — authorize, token,
  register and revoke handlers, PKCE, redirect checks, error formats — over a
  provider protocol. `LemmaAuthorizationServer` is that provider. The person
  signs in to Lemma the way they always do; the server only issues tokens.

**One URL per pod.** The URL is the RFC 8707 resource indicator, and the token
is bound to it, so a token for one pod is refused by every other pod. One URL
for all pods, with the pod picked on the consent screen, would save adding a
second connector, but every token would then have the same audience and the
pod would be a fact stored beside it rather than one the client asked for.
`/authorize` without a `resource` naming a pod on this server is refused with
`invalid_target`.

**Both registration methods.** Client ID metadata documents, because Claude and
ChatGPT prefer them. The document is fetched through fastmcp's SSRF-guarded
fetcher. Redirect URIs match exactly (OAuth 2.1 §4.1.1), save that a loopback
redirect's port may differ (RFC 8252 §7.3), which Claude Code relies on.
ChatGPT's document (`https://chatgpt.com/oauth/client.json`) declares
`private_key_jwt`, so a metadata-document client may authenticate at the token
endpoint with an RFC 7523 assertion signed by the keys its document publishes;
the assertion's audience may be the token endpoint or the issuer, and RS256 is
the one signing algorithm offered. A
dynamically registered client cannot use `private_key_jwt`: it has no document
to publish keys in.
Dynamic registration as well, because the spec keeps it as the fallback and
older clients use it. Registered client secrets are stored as SHA-256 digests.

**Tokens are opaque and stored as digests, and never outlive the grant's
terms.** Every MCP request already reads
the database to authorize the tool call, so one indexed lookup more buys what a
JWT cannot: revocation takes effect on the next request. Access tokens last an
hour. Refresh tokens last 30 days, rotate on every use, and a rotated refresh
token presented again ends the whole grant (OAuth 2.1 §4.3.1) — except once,
within 60 seconds of rotating, when it is the same client retrying a response
it lost. A retry is logged, and cancels every pair the grant holds before
issuing its own, under a lock on the grant, so a grant never has two usable
pairs; a second retry, or a cancelled refresh token presented later, is a replay
like any other. A replay is answered where the token is exchanged, not where it
is looked up, so a client revoking with a rotated token is disconnecting, not
logged as a thief. Rotating a refresh token and writing its replacement is one
transaction. Every token is
narrowed by the grant's current scopes when it is issued and when it is used,
so a grant never hands out more than the person agreed to; an empty list is no
scopes, never all of them. Each rotation prunes the grant to its live tokens,
the one refresh token just rotated (the one a replay would present) and any a
retry cancelled (which the losing party still holds, and must still be caught
presenting), so a grant holds a handful of rows however often it refreshes. An hourly job ends grants whose refresh token lapsed unused, and
every grant ends 180 days after consent however regularly it is used.
`private_key_jwt` assertion ids are claimed in Redis, so an assertion is used
once across every replica rather than once per process. Tokens carry a
`lemma_mcp_at_` / `lemma_mcp_rt_` prefix, which is how the endpoint tells them
from Lemma session tokens without asking SuperTokens, and how a secret scanner
recognises a leaked one.

**Scopes: `pod:read` and `pod:write`.** The pod tools split cleanly into those
that read and those that write, and a finer set would ask a person to reason
about tools they have never seen. A client granted only `pod:read` is not shown
the writing tools and is refused if it calls one. `offline_access` is accepted
and ignored: refresh tokens are always issued, and the spec asks servers not to
advertise it.

**Authorization is the person's own.** A token resolves to the person and the
pod, and tool calls run exactly as a Lemma session with no delegation claims
does: through the pod's own assistant, which is user-equivalent, so the
person's roles, resource grants and row-level security all apply. Consent
additionally checks that the person can read the pod, as a courtesy refusal at
the door rather than the enforcement. On every request and every refresh the
account's standing and the pod's liveness are checked too, so deactivating an
account or deleting a pod stops its connected clients at once; deletion is
soft and memberships survive it, so the pod needs its own check. A `pod.deleted`
subscriber then ends the pod's grants.

**The toolset is the pod toolset, annotated.** Every tool carries a title and
`readOnlyHint`, `destructiveHint`, `idempotentHint` and `openWorldHint`
(`app/modules/agent/services/pod_mcp_tool_policy.py`). A test fails if a pod
tool has no row there, because a missing row would default a new tool to the
cautious "write" policy silently. For outside clients the `needs_approval`
hand-off is removed from denied results: it points a Lemma agent at
`request_approval`, a tool outside clients do not have, and for someone acting
as themselves a denial is the answer. One call needs more than its tool's
scope: `pod_get_file_url` with `url_type: public` mints a link anyone can open
for up to a week, which outlives the connection, so it needs `pod:write`, and
the tool is marked open-world.

**An outside client's token is honoured only where it was checked.** The public
mount verifies the token, applies the rate limit and the Origin check, and
hands the service the principal it found on the request's state. The service
never reads an outside client's token from a header itself, and both mounts
refuse a request with two `Authorization` headers: Starlette reads the first,
fastmcp the last, so the token checked and the token acted on could differ.

**Connections, revocation and admins.** One consent is one connection (a grant):
the same client on two devices is two connections, listed apart and ended apart,
so disconnecting — or a refresh-token replay on — one leaves the other working.
`GET /oauth/grants` lists a person's connections with scopes and when each was
last used; with `everyone=true`, a pod's admins (`pod.member.manage`) see every
member's. `DELETE /oauth/grants/{id}` ends one — the person's own, or any in a
pod they administer — and deletes its tokens. A client can also revoke through
`/oauth/revoke`, which ends its own grant; it needs no `client_secret`, since
neither a public client nor a `private_key_jwt` one has one to send. The
feature stays on by default
(`MCP_ACCESS_ENABLED`); a per-organization switch is a possible follow-up.

**Redirects are checked on every path.** The consent page sends a signed-in
browser to the client's redirect URI, on the auth site, so a URI that runs code
(`javascript:`, `data:` and relatives) would run it with the person's session.
`domain/redirects.py` allows `https`, `http` to loopback only, and an app's own
scheme; it is applied at registration, to every metadata document's list, on an
exact match, on the single-URI default, and again as the redirect leaves. The
consent page refuses any `redirect_to` that fails the same rule, and `/auth/*`
cannot be framed (`frame-ancestors 'none'`, RFC 9700 §4.16).

**Who is asking, as far as it can be checked.** The consent screen leads with
the host serving the client's metadata document — the one verified fact about
it — and shows the name the client gives itself only as its claim, stripped of
control and bidirectional-override characters and cut to 60. A dynamically
registered client is labelled unverified. Where the person will be sent back to
is in the main text, not the small print: a web redirect's host, or, for an
app's own scheme, the scheme — `evilapp://claude.ai/cb` is not going to
claude.ai. The redirect is checked before anything is granted. When the client asks to
change things, the person can allow reading only instead.

**Nothing is stored before consent.** Registration and authorize are reachable
without signing in, so neither writes to Postgres: a metadata document is only
fetched, and a dynamic registration waits in Redis for a day, holding only the
fields that are read, with caps on their number and length. OAuth request
bodies are capped at 16 KB before anything parses them; a held authorize
request carries an S256 `code_challenge` and at most 2 KB of `state`. A client
row is written when a person allows it.

**Audit.** Every tool call by an outside client logs
`agent.pod_mcp_service.external_tool.called` with the client, the connection,
the person, the tool and the outcome — done, failed, or refused for scope. The
connection names the pod, and outlives its revocation. Records and their events
carry the person, not the app, so this line is where "that change came from
ChatGPT" is written; a column for it on records is a possible follow-up. Consent
granted and refused, connections ended (by the person, which admin, the client,
or the pod's deletion), registrations with their client id, refresh retries,
and refresh-token and client-assertion replays are logged too. Assertion ids
are claimed in Redis; fastmcp's per-process cache of them is switched off, as
it refused everyone once it held 10,000.

**Rate limits.** Per grant, 300 MCP requests a minute
(`MCP_ACCESS_REQUESTS_PER_MINUTE`), answered with 429 and `Retry-After`. Token
and revocation requests are limited per client *and* per source address (600 a
minute): Claude and ChatGPT refresh for all their users from a few shared
addresses, so an address alone would make every one of their users share one
budget. The caller chooses the `client_id`, though, and a new URL-shaped one
costs a fetch of its metadata document, so an address also has a ceiling of its
own across every client it names (6,000 a minute), checked first. Sign-in
requests (60 a minute) and dynamic registrations (300 an hour) are limited per
address, since each holds something in Redis — a pending request, a pending
client — before anyone has authenticated. Limits fail open if
Redis is unavailable: they protect the service, they do not authorize.

**The Agent Host's mount is unchanged.** The same FastMCP app answers at
`/agent-runtime/pods/{id}/mcp`, where a bad session token is still a JSON-RPC
error, as the Agent Host expects. The public mount answers 401 with the
challenge instead — the only thing that makes Claude start signing in — and
also validates `Origin`, as the transport spec requires.

## The exchange

```mermaid
sequenceDiagram
    participant C as MCP client
    participant A as Lemma API
    participant P as Auth portal (/auth/authorize)
    C->>A: POST /mcp/{pod} (no token)
    A-->>C: 401 WWW-Authenticate: resource_metadata=…, scope="pod:read pod:write"
    C->>A: GET /.well-known/oauth-protected-resource/mcp/{pod}
    C->>A: GET /.well-known/oauth-authorization-server
    C->>A: (metadata document id, or POST /oauth/register)
    C->>A: GET /oauth/authorize?…&resource=…/mcp/{pod}&code_challenge=…
    A-->>P: 302 /auth/authorize?request=<id>
    P->>A: GET /oauth/consent/{id} (session)
    P->>A: POST /oauth/consent/{id} {allow}
    A-->>P: redirect_to = client callback ?code&state&iss
    C->>A: POST /oauth/token (code, code_verifier, resource)
    C->>A: POST /mcp/{pod} Bearer lemma_mcp_at_…
```

Pending authorizations (10 minutes) and codes (5 minutes, single use) live in
Redis. Clients, grants and token digests live in `mcp_oauth_clients`,
`mcp_oauth_grants` and `mcp_oauth_tokens`.

## Connecting a client

Everything below is also on each space's **Settings › AI tools** page, with the
space's own link and a Copy button. The link is `https://<api>/mcp/<pod id>`;
`GET /oauth/mcp-endpoint/{pod_id}` returns it for a signed-in person.

- **Claude** (claude.ai, Desktop): Customize › Connectors, then Add › Add
  custom connector. Name it, paste the link, choose Add, then Connect, and
  allow Lemma. Claude detects OAuth and uses its own published identity; leave
  both as detected.
- **ChatGPT**: Plugins, then Add › Create MCP App. Name it, paste the link
  under Connection, tick "I understand", choose Create, then Continue, and
  allow Lemma. Mention it in a chat with `@<name>`. ChatGPT picks the client
  metadata document and `private_key_jwt` on its own.
- **Claude Code**: `claude mcp add --transport http lemma <link>`, then `/mcp`
  in a session (or `claude mcp login lemma`), and allow Lemma in the browser.
- **Anything else**: add the link as a remote MCP server. A client that
  follows the MCP authorization spec needs nothing more.

Claude and ChatGPT connect from their own servers, so the API must be
reachable from the public internet; the settings page says so when it is not.

## Deploying behind a path prefix

`API_URL` may carry a path (`https://lemma.example/api`), with a proxy stripping
the prefix before the API sees it. The pod URL is then `…/api/mcp/<pod id>` and
the issuer `…/api`. Two things must reach the API unchanged, because RFC 8414
and RFC 9728 put them at the host root: `/.well-known/oauth-authorization-server/api`
and `/.well-known/oauth-protected-resource/api/mcp/<pod id>`. Set
`SUPERTOKENS_API_GATEWAY_PATH=/api/st` to match. This is how the connector was
tested against claude.ai and ChatGPT through a single tunnel.

## Events

A connected client can subscribe to a pod's events: the webhook slice of the
MCP Events working-group draft, which is what ChatGPT implements. It is a
draft with no SEP, so it is kept to `app/mcp_events.py` (the wire) and
`mcp_access` (everything it decides).

- **Advertised** as `capabilities.events` on `server/discover`. The SDK sieves
  spec results against the spec's model, which has no `events`, so a FastMCP
  middleware adds it after the sieve. The methods are a FastMCP server
  extension (`work.lemma/events`) gated to protocol 2026-07-28.
- **One event**, `record.created`, with one argument, `table`. A client may
  subscribe only to a table the person can read at that moment
  (`-32012 Forbidden` otherwise).
- **Subscribing** takes the client's own `whsec_` secret (24 to 64 bytes) and a
  callback URL that must pass the SSRF guard. Before anything is stored the
  callback is sent a signed `{"type": "verification", "challenge": ...}` and
  must echo the challenge, compared in constant time; a URL this connection
  already proved is not challenged again. Identity is `(grant, url, name,
  arguments)`: subscribing again with the same four refreshes the row and may
  rotate the secret. `refreshBefore` is never more than the client asked for,
  never more than 24 hours, never null. At most 50 per connection
  (`-32013`). The secret is stored encrypted.
- **Delivering** is one worker job per subscriber per row, fanned out from the
  datastore stream after a cheap "is anything listening to this pod" check.
  Every delivery re-checks the grant (live, still `pod:read`) and reads the row
  as the person under their permissions and row-level security; a row they
  cannot read is never sent. The body is the draft's `EventOccurrence`,
  Standard Webhooks signed over the exact bytes, `webhook-id` = the source
  event id on every retry, capped at 256 KiB (a larger row is sent as its id).
  Redirects are refused. 2xx is delivered; 410 ends the subscription; 413 and a
  URL the guard now refuses are dropped; anything else is retried with backoff,
  five tries in all.
- **Ending**: `events/unsubscribe` (idempotent), Stop beside the subscription
  in the person's connected apps (`DELETE /oauth/grants/{id}/subscriptions/
  {sub}`), revoking the connection (deliveries stop at once, the rows go with
  the grant), or the client letting `refreshBefore` pass. `terminated` is not
  sent: ChatGPT does not handle it, and the next delivery's re-check is what
  enforces revocation.

## Not built yet

- **More MCP Apps views, a Claude Code plugin, directory listings.** Planned,
  in order, in [A pod as a plugin](mcp-plugin.md#what-comes-next-in-order),
  including what one URL per pod means for a directory listing.
- **A scenario journey.** The product-spec entries are PS-ACCESS-040 and
  PS-ACCESS-041. The module e2e test drives the whole exchange in-process; the
  `tests/scenarios/` journey over a real socket has not been built yet.
- **A resource check at the token endpoint.** The SDK does not pass the token
  request's `resource` to the provider, so a client that names a different
  resource at the token step than at authorize still gets a token for the one
  it was authorized for. The audience is never wider than what the person
  consented to.
