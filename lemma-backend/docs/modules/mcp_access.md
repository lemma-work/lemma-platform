# MCP access module

Lets a person add one of their pods to an outside MCP client — Claude, ChatGPT,
an editor — by URL, and sign in to Lemma to allow it. The whole design, with
what each client requires, is in
[Pods as remote MCP servers](../../../docs/architecture/mcp-connector.md).

## What it owns

- The OAuth 2.1 authorization server under `/oauth`: authorize, token,
  dynamic registration, revocation, and the discovery documents at
  `/.well-known/oauth-authorization-server` (and its OpenID alias) and
  `/.well-known/oauth-protected-resource/mcp/{pod_id}`. The HTTP handlers are
  the MCP SDK's; `LemmaAuthorizationServer` is the provider behind them.
- Client resolution: dynamically registered clients, and client ID metadata
  documents fetched through fastmcp's SSRF-guarded fetcher.
- Consent: `GET`/`POST /oauth/consent/{request_id}`, answered from the auth
  portal's `/auth/authorize` page with the person's session. Portal-only, so
  out of the public schema.
- Grants: `GET /oauth/grants` and `DELETE /oauth/grants/{id}`, the person's
  connected clients and how they end one.
- Checking an access token on a pod's MCP endpoint, and the per-grant and
  per-IP rate limits.

Tables: `mcp_oauth_clients`, `mcp_oauth_grants`, `mcp_oauth_tokens` (digests
only). Pending authorizations and codes are short-lived Redis keys.

## What it does not own

The MCP endpoint itself. `app/mcp_server.py` mounts one FastMCP app twice: at
`/agent-runtime/pods` for the Agent Host and at `/mcp` for outside clients. It
reaches this module through `mcp_access/contracts`. Which tools a pod exposes,
and what scope each needs, belongs to `agent`
(`services/pod_mcp_service.py`, `services/pod_mcp_tool_policy.py`).
Authorization of each tool call is the person's own, built by core
authorization exactly as for a Lemma session.
