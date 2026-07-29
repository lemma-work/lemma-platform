# Unified Runtime Profiles, Agent Hosts, and Harnesses

## Product model

Lemma uses three separate concepts:

- A **runtime profile** is saved execution configuration selected by an agent.
- An **Agent Host** is a paired machine running the Agent Host service.
- A **harness** is a local agent discovered on an Agent Host, such as Codex,
  Claude Code, OpenCode, or Cursor.

Provider-backed and harness-backed execution share the same runtime-profile
resource. An Agent Host is infrastructure and is never a runtime-profile type.
A harness-backed profile binds directly to a harness ID.

Workspaces are also infrastructure. They are not profiles or harnesses.

## Runtime profiles

The profile discriminator is `runtime_type`:

- `OPENAI_COMPATIBLE`
- `ANTHROPIC_COMPATIBLE`
- `AZURE_OPENAI`
- `GOOGLE_VERTEX`
- `HARNESS`

A persisted profile contains identity, organization, optional personal owner,
scope, runtime type, status, name, description, optional harness binding,
optional default model, provider model catalog, typed configuration, and
encrypted credentials.

Personal profiles require `owner_user_id`. Organization profiles have no
profile owner. Provider profiles require a default model and cannot bind a
harness. Harness profiles require a harness ID and may omit a model; a null
model means the harness chooses its own default.

Model choice has one source of truth: `default_model_name` on the profile and
the optional `model_name` override in an agent's minimal selection:

```json
{"profile_id": "profile-id", "model_name": "optional-explicit-model"}
```

Harness configuration selections never contain the model. Provider model
catalogs are stored; harness catalogs and availability are derived from the
current harness snapshot.

Runtime type, scope, owner, and harness binding are immutable. Deletion disables
a profile so historical references remain valid. Explicit models are validated
strictly and are never replaced silently.

## Ownership and delegation

Agent Hosts are paired to a user and organization. Harness ownership is derived
through `harness -> agent_host -> user`; it is not duplicated on profiles.

Creating an organization-scoped harness profile is the explicit delegation
boundary. Any organization member may use it while all of these remain true:

- the host is paired to that organization;
- the host owner remains an organization member;
- the host is online and not revoked or draining;
- the harness is healthy and fresh;
- the saved configuration revision matches the current snapshot.

## Canonical APIs

Profile management:

```text
GET    /organizations/{org_id}/runtime/profiles
POST   /organizations/{org_id}/runtime/profiles
GET    /organizations/{org_id}/runtime/profiles/{profile_id}
PATCH  /organizations/{org_id}/runtime/profiles/{profile_id}
DELETE /organizations/{org_id}/runtime/profiles/{profile_id}
POST   /organizations/{org_id}/runtime/profiles/{profile_id}/refresh
```

Agent Host management:

```text
POST   /me/runtime/agent-host-pairings
GET    /me/runtime/agent-hosts
DELETE /me/runtime/agent-hosts/{host_id}
GET    /me/runtime/agent-hosts/{host_id}/harnesses
```

The device protocol uses stable routes under `/agent-host/*`. Protocol
negotiation is numeric and occurs in the handshake rather than in URLs.

Workspace reservation:

```text
GET    /me/runtime/workspaces/default
PUT    /me/runtime/workspaces/default
DELETE /me/runtime/workspaces/default
POST   /me/runtime/workspaces/default/apps/browser/access
```

Multiple workspace records are intentionally deferred until the product
supports multiple workspaces per user.

## Harness snapshots

An Agent Host publishes revisioned harness snapshots containing:

- harness ID, host ID, stable harness key, and display name;
- adapter protocol `ACP` and a separate numeric protocol version;
- adapter and upstream versions;
- capabilities and configuration options;
- configuration revision;
- health, freshness, and a bounded failure reason.

Native ACP harnesses run direct executables after minimum-version validation.
Packaged adapters are installed into a verified cache from exact lockfile
versions and integrity values during installation, upgrade, or repair. A run
never downloads code.

## Dispatch reliability

The server queues a command with a lease. Before local process dispatch, Agent
Host either durably accepts it or writes a durable rejection receipt containing
command, run, and lease identity, a bounded code, and retryability.

Retryable pre-dispatch rejection atomically returns the command to the queue.
Permanent pre-dispatch rejection may invoke a configured provider fallback.
Duplicate, stale, or reordered receipts are idempotent and cannot roll back an
acceptance. Once dispatch is durable or may have reached a provider, Lemma never
repeats the run.

Host capacity is global across every paired target. Draining, expiry, missing
harnesses, stale revisions, invalid selections, and lost capacity all use the
same rejection protocol.

Graceful shutdown advertises zero capacity, stops accepting commands, flushes
events/checkpoints/receipts, waits for active work within its deadline and
shutdown grace, then cancels remaining subprocesses without fallback.
Unexpected post-dispatch crashes produce `DISPATCH_UNKNOWN`.

ACP session loading may support future cross-turn continuity, but it does not
make in-flight recovery safe. Agent Host reports
`durable_session_recovery=false`.

## Retention

Daily cleanup removes:

- expired pairing artifacts after 24 hours;
- expired terminal-run MCP routes after 24 hours;
- terminal commands, events, leases, and local journal records after 30 days;
- acknowledged event-outbox rows immediately.

Active runs are never collected.

## Breaking migration

This release removes the former local daemon runtime without compatibility
routes or response fields. Migration resets saved future selections that refer
to removed local runtimes, preserves historical run snapshots, terminalizes
active legacy runs, deletes obsolete profiles, drops obsolete storage, and
renames Agent Host harness storage consistently.

Local default files that point to removed profiles are ignored with a warning
and resolve to the configured system default.
