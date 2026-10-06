# Connectors module

## Purpose

`app/modules/connectors` manages third-party application capabilities. It owns
the connector/operation/trigger catalog, organization auth configurations,
user-owned connected accounts, OAuth/credential flows, operation discovery,
and operation execution routing between native Lemma integrations and Composio.

## Runtime contributions

The module contributes nine API routers, no event consumer, and one cron
(`renew_mcp_event_subscriptions`, every five minutes).
Long external operation calls use a resolve -> external call -> persist saga so
a pooled database connection is not held during provider I/O.

## Main data model

| Table | Meaning |
| --- | --- |
| `connectors` | Provider-independent application catalog entry |
| `connector_operations` | Searchable operation metadata and JSON input/output schemas |
| `connector_triggers` | Available external trigger metadata |
| `auth_config_operations` | Operations discovered for one organization's install. MCP tools and OpenAPI-URL endpoints describe a customer's own systems, so they are kept out of the global catalog entirely rather than held there behind a nullable tenant column |
| `auth_config_events` | Events an MCP install's server lists with `events/list`, discovered beside its tools and kept per install for the same reason |
| `connector_event_subscriptions` | One subscription to an MCP server's event, made on a person's account for one schedule: our id (in the callback URL), our encrypted `whsec_` secret, the server's id and `refreshBefore` |
| `auth_configs` | Organization installation/configuration and encrypted OAuth client secrets |
| `accounts` | User-owned encrypted provider credentials and connection status |
| `connect_requests` | Short-lived OAuth state and authorization flow metadata |

## API groups

| Routes | What they do |
| --- | --- |
| `/connectors` | Public catalog, connector detail, and generated skill text |
| `/organizations/{org}/connectors/status` | Installed/connected capability summary |
| `/.../auth-configs` | Create/list/get/delete organization auth configurations |
| `/.../accounts` | Create/list/get/delete user accounts and resolve account credentials |
| `/.../connect-requests` + callback | Start OAuth and exchange callback state/code |
| `/.../{auth_config}/operations` | Search/details and execute an operation |
| `/.../{auth_config}/triggers` | Discover provider triggers |

A trigger's `config_schema` is returned by the single-trigger endpoint, not by
the list — the list is deliberately lean. It declares the parameters a schedule
may narrow the trigger by, and those go at the top level of the schedule's
config, which is what the routing key is matched against. Keys a person cannot
sensibly supply are bound when the schedule is created: GitHub's
`installation_id` comes from the connected account's `external_ref`, which is
where the App install redirect recorded it.

## OAuth and execution flows

```mermaid
sequenceDiagram
    participant U as User
    participant API as Connector API
    participant DB as PostgreSQL
    participant P as Native/Composio provider
    U->>API: start connect request
    API->>DB: persist state + pending request
    API-->>U: provider authorization URL
    U->>P: consent
    P->>API: callback(code, state)
    API->>DB: resolve pending request/config
    API->>P: exchange code
    API->>DB: encrypt credentials + connect account
```

For execution, a short UoW resolves the auth config, operation, account, and
decrypted credential values into a session-free DTO. The provider call happens
outside a UoW; a final short UoW marks accounts for reauthentication when an
unauthorized response is classified.

A credential refresh fails one of two ways, and they answer differently. When
the provider says the grant itself is gone (`invalid_grant`, `invalid_client`,
`unauthorized_client`, a Composio connection in a terminal state), or an expired
token has nothing to refresh with, the account is marked `REAUTH_REQUIRED` and
the caller gets a 409 `CONNECTOR_REAUTH_REQUIRED` naming the account to
reconnect. A provider that is unreachable or answers 5xx is a 502
`OAUTH_FLOW_ERROR`, because retrying may help and reconnecting will not.

A Composio toolkit's `system_default_available` can turn false on a later
catalog import. Connecting through a Lemma-default install of such a toolkit is
refused with the same 400 as creating one, and the importer disables those
installs and flags their accounts `REAUTH_REQUIRED`, so the organization is
asked to bring its own OAuth app.

## Authorization and secrets

- Auth configurations are organization-scoped; accounts remain owned by one
  user even when the auth configuration is shared.
- Credentials are encrypted by the shared versioned secret cipher.
- Operation execution resolves the caller's permitted account and scopes before
  calling a provider.
- Provider-specific capabilities live behind auth-provider and operation-gateway
  adapters.

## Tests and operations

Tests cover native and Composio routing, OAuth state, account identity,
credential encryption/refresh, catalog import, and operation timeouts. Dynamic
connector schemas are parsed by a restricted AST reader, never `exec()`; see
`infrastructure/schema_compiler.py` and its hostile-input tests.

## MCP server events

An MCP install's server may offer events through the MCP Events working-group
draft (webhook slice, revision 2026-07-28) — the same draft a pod speaks to
ChatGPT from `mcp_access`. Here the pod is the subscriber:

- **Discovery.** `events/list` is called beside `tools/list` whenever the
  install's tools are discovered; a server that offers none (method not found,
  an older revision) is an install with no events, not a failed discovery. The
  descriptors go to `auth_config_events`, never to the global trigger catalog:
  the one `mcp` connector is every server anyone connected.
- **Subscribing.** `contracts/mcp_events.subscribe_to_mcp_event` is what a
  WEBHOOK schedule with `config.source = "mcp"` calls when it is created. It
  checks the event is offered and its required arguments present, commits a
  pending row with a fresh secret, then calls `events/subscribe` with the
  callback `{api_url}/webhooks/mcp?subscription=<our id>`. The commit comes
  first because the server proves the callback with a signed challenge before
  it answers. Our id becomes the schedule's `provider_trigger_id`.
- **Delivery.** `webhook_sources/mcp.py` verifies each request against that
  subscription's secret (Standard Webhooks, five-minute skew), checks the
  server's `X-MCP-Subscription-Id` once it has named one, echoes a challenge,
  and routes an occurrence by our id with `mcp:<id>:<eventId>` as its
  `source_event_id`. `gap` and `terminated` notices run nothing.
- **Renewal.** The cron renews each subscription halfway through what the
  server granted, by subscribing again with the same identity; a failure is
  noted on the row. Unsubscribing (schedule edited or deleted) is best effort
  at the server and certain here: the row goes, so a late delivery verifies
  against nothing.
- **Calls to the server** are raw JSON-RPC POSTs
  (`infrastructure/adapters/mcp_events_client.py`) through the SSRF guard, with
  redirects refused, and the account's credentials as headers.
