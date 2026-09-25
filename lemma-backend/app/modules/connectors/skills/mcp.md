# MCP Server

A generic catalog entry: point an install at an MCP server's streamable-HTTP
endpoint and **each of its tools becomes an operation**. The operation set comes
from the server, so it differs per install and is not listed here — discover it.

**Install config:**

| Field | |
| --- | --- |
| `server_url` | **Required.** The streamable-HTTP endpoint, e.g. `https://mcp.acme.test/mcp`. |
| `extra_headers` | Optional. Sent with every request. |
| `session_setup` | Optional. Tool calls replayed at the start of every session, before anything else. |

`session_setup` is for servers that only expose some of their tools after a
session-scoped call — Arize Phoenix's `enable_tool_group`, for instance. The
tools it unlocks are discovered as operations like any other:

```json
{"server_url": "https://mcp.acme.test/mcp",
 "session_setup": [{"tool_name": "enable_tool_group", "arguments": {"group": "admin"}}]}
```

## How it authenticates is the server's decision, not this entry's

This is the one thing to get right. This catalog entry says `API_KEY`, but a
single entry stands for every server a tenant might point at, and they do not
agree. So Lemma asks: at install time it tries RFC 9728 → RFC 8414 → RFC 7591
**dynamic client registration**. A server that describes its own authorization
gets registered as an OAuth client, and its people sign in through a browser. A
server that does not stays paste-a-token. Registration failing is never fatal.

**Read `auth_scheme` on the install** and branch on that, never on the kind:

```bash
lemma connectors auth-configs get acme-mcp     # auth_scheme: OAUTH2 | API_KEY
```

Posting an empty credential set to a server that wanted a sign-in produces an
account that looks connected, holds no token, and fails every call.

## Set one up

```bash
lemma connectors auth-configs create mcp --kind mcp --name acme-mcp \
  -d '{"server_url": "https://mcp.acme.test/mcp"}'

# API_KEY install — the token is optional, and some servers need none:
lemma connectors accounts create --auth-config acme-mcp -d '{"bearer_token": "..."}'

# OAUTH2 install — hand the person the link and wait for the account:
lemma connectors connect-requests create mcp --auth-config-id <id>
lemma connectors accounts list --app mcp
```

Then `operations search` / `details` / `run`, as with any connector.

## Tips

- **Tools are discovered at install time.** When the server gains one, run
  `lemma connectors auth-configs refresh-operations acme-mcp` and read `status`.
- Each operation's execution descriptor is `{"kind": "mcp", "tool_name": "..."}`
  — the operation id you run is Lemma's, the tool name is the server's.
- Credential precedence, highest first: the account's OAuth token, then the
  account's bearer token. The token belongs to the account, not the install —
  the install schema is closed and has no field for one.
- **Private, loopback and link-local addresses are refused.**
- Changing `server_url` sends the install's accounts to `REAUTH_REQUIRED`.
- **File arguments.** MCP has no file type of its own. A tool field declared as
  a base64 string (`contentEncoding: base64`) takes a pod file reference,
  `{"pod_path": "/me/report.pdf"}`, and receives the file's bytes base64
  encoded. Any other field is passed through untouched.
