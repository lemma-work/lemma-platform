# Bring-your-own connectors: SQL, OpenAPI and MCP in the frontend

**Status:** Implemented · **Surface area:** `lemma-frontend` (the bulk), plus the
backend catalog importer

## The change in one sentence

An org admin can point Lemma at *their own* database, REST API or MCP server —
supplying the address, not picking a logo — and manage those connections
afterwards, which today is impossible from any screen in the product.

## Why this is a gap and not a feature request

The backend shipped all three kinds. [`ConnectorKind`](../../lemma-backend/app/modules/connectors/domain/connector.py)
has `SQL`, `HTTP` and `MCP` alongside `COMPOSIO` and `PACKAGE`; the
[kind registry](../../lemma-backend/app/modules/connectors/infrastructure/kinds/registry.py)
wires each to an executor, an installer that vets the tenant-supplied network
target, and — for MCP and OpenAPI — a discoverer that turns a live server into an
operation set. [`lemma_apps_config.json`](../../lemma-backend/scripts/lemma_apps_config.json)
carries three catalog rows for them, and the TypeScript SDK already exposes
every endpoint they need, including `update`, `delete` and `refreshOperations`
([connectors.ts:149](../../lemma-typescript/src/namespaces/connectors.ts)).

What is missing is the one thing these kinds need that no other kind does: **a
place to type the address.** Composio and package connectors are fully described
by the catalog — Slack is Slack. A SQL connector is nothing until someone says
which host, and the frontend never asks.

### What actually happens today

The catalog rows do render — `sql`, `mcp` and `openapi` appear in *Browse apps*,
in the ungrouped "More apps" tail, with a generated monogram instead of a logo.
Clicking **Connect** on "SQL Database" runs this path in
[connectors-view.tsx:180](../../lemma-frontend/components/connectors/connectors-view.tsx):

1. `usesDirectCredentials()` is true (`auth_scheme: API_KEY`), so no OAuth.
2. `hasSystemDefault()` is *also* true — the importer sets
   `system_default_available = auth_method != OAUTH2`, which is a statement about
   OAuth clients that reads as "this connector is ready to go".
3. So the credential dialog opens immediately, asking for a database **username
   and password**.
4. On submit, [connectors-view.tsx:290](../../lemma-frontend/components/connectors/connectors-view.tsx)
   creates the install with `config_source: SYSTEM_DEFAULT` **and no `config`**.
5. The backend validates that empty config against the install schema, which
   requires `dialect`, `host` and `database`, and returns
   `Invalid install config`.
6. The user sees `Failed to save credentials`.

You are asked for the password to a database you were never allowed to name. The
**Advanced** escape hatch is not offered either: `hasAdvancedOptions()` is false,
because `supportsCustomConfig()` in
[connector-utils.ts:93](../../lemma-frontend/components/connectors/connector-utils.ts)
gates custom config on `supports_org_custom_oauth` — a flag the importer sets
only for OAuth2 connectors. The frontend's whole notion of "custom
configuration" is welded to "the org brought its own OAuth app", and for these
three kinds the config *is* the connection.

MCP and OpenAPI fail identically, with a shorter form: an optional bearer token,
then the same failure for a missing `server_url`.

### A backend one-liner blocks it too

Even if the dialog collected a host, the install would be the wrong kind.
`_native_kind_spec()` selects the spec class — and with it the executor,
installer and discoverer — from a `kind` argument
([import_connector_catalog.py:613](../../lemma-backend/scripts/import_connector_catalog.py)),
but the native-catalog sync at line 1143 never passes it:

```python
_native_kind_spec(
    auth_method=auth_method,
    oauth2_defaults=app_config.get("oauth2_config"),
    auth_config_schema=app_config.get("auth_config_schema"),
    credential_schema=app_config.get("credential_schema"),
    system_oauth=app_config.get("system_oauth"),
    profile_operation_names=...,
)   # no kind= — falls through to PackageKindSpec
```

`"kind": "sql"` in the catalog JSON is dead data. Nothing in the repo reads it.
The three rows are imported as `package`, so an install would dispatch to the
vendored-client gateway rather than to `SqlExecutor` / `McpExecutor` /
`OpenApiHttpExecutor`, and MCP/OpenAPI discovery would never run. This is one
keyword argument, but nothing downstream works without it.

## The model the UI has to catch up with

Three facts about these kinds that the current screens contradict:

| Backend fact | Where it lives | What the frontend assumes |
| --- | --- | --- |
| An org holds **many installs of one connector** — several MCP servers, two databases | [connector_service.py:530](../../lemma-backend/app/modules/connectors/services/connector_service.py) | `enabledConfigByAppId` is a `Map` keyed by `connector_id` — last install wins ([connectors-view.tsx:119](../../lemma-frontend/components/connectors/connectors-view.tsx)) |
| Installs are **named and editable**; rotating a URL keeps the accounts attached | `connector.auth_config.update` | No hook, no screen. The only lifecycle verb in the UI is "Disconnect account" |
| MCP/OpenAPI installs **discover** their operations, and discovery can fail silently | `connector.auth_config.refresh_operations` | `useConnectorOperations` exists in [use-connectors.ts:224](../../lemma-frontend/lib/hooks/use-connectors.ts) and has **no callers** |

The comment at [use-connectors.ts:75](../../lemma-frontend/lib/hooks/use-connectors.ts)
— "An org holds at most one auth config per app" — is now false, and the trigger
hooks that rely on it resolve to an arbitrary install.

## Proposed shape

**One dialog, two sections, one submit.** The split between "enable the
connector" and "connect an account" is a backend distinction (install vs.
credentials) that these kinds have no reason to expose: for a database you type
the host and the password in one sitting. The dialog collects
`auth_config_schema` fields and `credential_schema` fields together, then calls
`enableApp` followed by `accounts.create`. An account is always required —
execution resolves one even for a no-auth MCP server — so a connector with an
empty credential schema simply renders no second section and creates the account
with `{}`.

```
Add MCP server                                    Add SQL database
─────────────────────────────                     ─────────────────────────────
Name       Sentry tools                           Name       Analytics replica
Server URL https://mcp.example.com/               Host       db.internal:5432
Bearer     ••••••••                               Database   analytics
                                                  User       readonly
                              [Cancel] [Add]      Password   ••••••••
                                                                [Cancel] [Add]
Then: "Found 14 tools."                           Then: "3 operations available."
```

The name field is the load-bearing new control. With many installs per connector,
the name is the only thing telling "Analytics replica" from "Billing replica" —
in this dialog, in the accounts list, and in the agent access picker, where three
SQL accounts currently render as three rows all reading "SQL Database".

**Where it lives.** These are not apps you browse for; you arrive knowing you
have a database. They belong above the catalog as their own affordance —
`Add your own · [Database] [API] [MCP server]` — not as three cards in the "More
apps" tail. The catalog rows stay in the grid (they are legitimately catalog
entries) but stop being the entry point.

**Post-add feedback is the payoff.** MCP and OpenAPI discovery runs
server-side after the install commits and swallows its failures by design — a
failed discovery leaves a usable, empty install. So the dialog must report what
came back ("Found 14 tools" / "Connected, but no tools were found — retry
discovery"), and the install row needs a **Refresh operations** action. Without
it, the recovery path the backend deliberately built is unreachable.

**Installs need a list.** A section under "Your accounts" showing each install by
name, its kind, its target (`https://mcp.example.com/`, `db.internal/analytics`),
its operation count, and actions: *Edit*, *Refresh operations*, *Delete*. Edit
maps to `authConfigs.update`, which is the whole reason that endpoint exists —
deleting and recreating an install cascades away every account on it.

## Work breakdown

| # | Change | Files | Size |
| --- | --- | --- | --- |
| 1 | Pass `kind=app_config.get("kind")` in the native sync; assert the three rows import as `sql`/`mcp`/`http` | [import_connector_catalog.py:1143](../../lemma-backend/scripts/import_connector_catalog.py), new unit test | XS |
| 2 | Re-run the catalog import against existing environments (the rows already exist as `package`) | ops | XS |
| 3 | Teach `supportsCustomConfig` that a non-OAuth kind with config fields always supports custom config; stop `hasSystemDefault` from reading as "ready to connect" for tenant-configured kinds | [connector-utils.ts](../../lemma-frontend/components/connectors/connector-utils.ts) | S |
| 4 | Hooks: `useUpdateAuthConfig`, `useDeleteAuthConfig`, `useRefreshAuthConfigOperations`; fix the stale single-install assumption in `useTriggers` / `useTrigger` | [use-connectors.ts](../../lemma-frontend/lib/hooks/use-connectors.ts) | S |
| 5 | `AddConnectionDialog` — config + credential sections, name field, one submit, discovery result surfaced | new `components/connectors/add-connection-dialog.tsx` | M |
| 6 | "Add your own" entry point above the catalog, three kinds | [connectors-view.tsx](../../lemma-frontend/components/connectors/connectors-view.tsx), [connector-grid.tsx](../../lemma-frontend/components/connectors/connector-grid.tsx) | S |
| 7 | Installs list with edit / refresh / delete; account rows labelled by install name | [connectors-view.tsx](../../lemma-frontend/components/connectors/connectors-view.tsx), [connector-card.tsx](../../lemma-frontend/components/connectors/connector-card.tsx) | M |
| 8 | Multi-install correctness: replace `enabledConfigByAppId` with a list; "Add another" opens a *new* install for these kinds rather than reusing the existing one | [connectors-view.tsx:119](../../lemma-frontend/components/connectors/connectors-view.tsx) | M |
| 9 | Header maps (`extra_headers`, `default_headers`) currently render as a raw JSON textarea — `buildSchemaFormFields` maps `type: object` to `kind: "json"`. A key/value row editor, or at minimum a placeholder showing the expected shape | [schema-fields.tsx](../../lemma-frontend/components/connectors/schema-fields.tsx) | S |

Items 1–5 are the minimum for "a user can add an MCP server and use it". 6–9 are
what stop it from being a one-shot action you can never revisit.

## Decisions to confirm

- **`config_source` for these kinds.** `ORG_CUSTOM` is the honest label — the org
  supplied the connection — but nothing branches on it for non-OAuth kinds
  ([_install_validation.py:82](../../lemma-backend/app/modules/connectors/infrastructure/kinds/_install_validation.py)),
  so `SYSTEM_DEFAULT` (what the code sends today) is equally functional. Cosmetic,
  but it lands in a persisted, immutable column.
- **Agent access granularity.** Grants are by `app_name` with an optional
  `account_id` ([value_objects.py:125](../../lemma-backend/app/modules/agent/domain/value_objects.py)).
  Pinning an account does select a specific install, so the model works — but a
  `DYNAMIC` grant on `sql` silently resolves to whichever install is default.
  Worth deciding whether the access dialog should force an account choice for
  multi-install connectors.
- **SSRF surface.** `assert_safe_url` / `assert_safe_host` already reject
  metadata endpoints and internal hosts at install time
  ([network_kinds.py:32](../../lemma-backend/app/modules/connectors/infrastructure/kinds/network_kinds.py)),
  and the error carries a `reason`. The dialog should surface that reason
  verbatim rather than a generic failure — "we won't connect to a private
  address" is the useful message.
