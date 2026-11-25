# Vault module

## Purpose

`app/modules/vault` is where the platform stores secrets: a row that uses one
holds only a `*_secret_id`, and reads and writes it through
`app.modules.vault.contracts` -- the only part other modules import.

## Data model

| Table | Holds |
| --- | --- |
| `vault_keys` | Key-encryption keys (KEKs), each wrapped by the root key, with purpose `encrypt` (wraps data keys) or `sign` (roots token signing when the root key is remote) and state `active`, `decrypt_only` or `retired`. At most one active key per purpose. |
| `vault_secrets` | One row per secret: owner scope (`organization_id`, `pod_id`, `user_id`), `purpose`, `kind` (text or JSON), the data key wrapped by a KEK, the AES-256-GCM ciphertext, `version`, non-secret `metadata`, `expires_at`, and a refresh lease (`lease_holder`, `lease_until`). The current version only: a new value replaces the old in place under a new data key. |
| `vault_secret_events` | The audit trail: created, replaced, revealed (when a person or workload asked), deleted (including by an owner row going away), rewrapped and imported, with actor and request id. |

Every stored secret lives here. Each owner and the purpose and scope its value
is bound to:

| Owner (`*_secret_id`) | Purpose | Scope |
| --- | --- | --- |
| `accounts.credentials_secret_id` | `connectors.account.credentials` | org, user |
| `auth_configs.config_secret_id` | `connectors.auth_config.config` | org |
| `connector_event_subscriptions.signing_secret_id` | `connectors.event_subscription.secret` | org, user |
| `agent_runtime_profiles.secrets_secret_id` | `agent.runtime_profile.secrets` (credentials and header values) | org |
| `agent_surfaces.webhook_secret_id` | `agent_surfaces.surface.webhook_secret` | org, pod |
| `surface_whatsapp_numbers.credentials_secret_id` | `agent_surfaces.whatsapp_number.credentials` | deployment |
| `slack_configuration_tokens.tokens_secret_id` | `agent_surfaces.slack_configuration.tokens` | org |
| `agent_surface_web_widgets.signing_secret_id` | `agent_surfaces.web_widget.signing_secret` | pod |
| `mcp_event_subscriptions.signing_secret_id` | `mcp_access.event_subscription.secret` | pod, user |
| `machine_login_backups.key_secret_id` | `workspace.login_backup.key` | sandbox owner |
| `machine_services.env_secret_id` | `workspace.machine_service.env` | sandbox owner |
| SuperTokens user metadata (no trigger; deleted on revocation) | `identity.apple.refresh_token` | user |

Until the next release's contract migration, each owner keeps its old Fernet
column too: a row the previous release wrote during the rollout is read from
there (`legacy_json` / `legacy_text`) and moved into the vault on its next
write, and a write to the old column by the previous release drops the vault
copy (migration `0078_vault_cutover`).

## How a secret is protected

```mermaid
flowchart TB
    R["Root key — Cloud KMS / keyset / keychain (never in Postgres)"] -->|wraps, once per process| K["KEK (vault_keys)"]
    K -->|wraps| D["Data key, fresh per value"]
    D -->|AES-256-GCM| V["Value, bound to secret id, version, kind, purpose, org, pod, user"]
```

- **Binding.** The associated data is the scope the *caller* expects, taken
  from the owner row, plus the purpose (the owning column). A row pointed at
  another tenant's secret, or a ciphertext copied onto another row, fails to
  decrypt. A mismatch is a not-found to callers and is logged.
- **Cost.** A read is one primary-key lookup and two local AES-GCM operations.
  KEKs are unwrapped when a process starts and kept in memory; the root key is
  never on the read path. Plaintext is never cached, in process or in Redis.
- **Lifecycle.** `vault_owned(table, *columns)` installs a trigger on the owner:
  deleting the owner row (by any path, including a cascade from an organization)
  or repointing its column deletes the secret in the same transaction.
- **Refresh.** `try_lease` / `replace(expected_version=…, lease=…)` give OAuth
  refresh a single flight: one caller refreshes, others wait for the version to
  move, and a stale writer gets a conflict instead of overwriting a newer token.
- **Short-lived values.** The sealer (`seal_value` / `open_json`) applies the
  same protection to values held outside the table — the sandbox environment
  cached in Redis, a GitHub installation token, Teams tokens, a pending Slack
  form, a kept-chat cookie, a queued agent-host MCP frame, the data key of a
  home carried across a machine size move — bound to the cache key, run or
  object they belong to. Rewrap does not reach them, so only values that expire
  are sealed; anything long-lived is a row here.

## Runtime contributions

The module is registered first. Its API and worker lifespans validate the root
configuration and load the keyring before anything else starts (a hosted
process with an unusable root refuses to start), then refresh it every
`VAULT_KEK_REFRESH_SECONDS` so a KEK rotated elsewhere is picked up. It has no
routes, events or tasks.

## Tests and operations

`tests/unit` covers binding, the sealer, signing keys and the migration's
legacy decoders; `tests/e2e` covers the store, the owner trigger, leases,
audit and rewrap.
Operators use `scripts/vault_admin.py` (status, probe, rotate-kek, rewrap,
retire-kek, rotate-sign-key); see `docs/operators/secrets-vault.md`.
