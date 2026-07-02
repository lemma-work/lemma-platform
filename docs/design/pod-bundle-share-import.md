# Pod Bundles: Export, Import, and GitHub Sharing

**Status:** Accepted · **Module:** `app/modules/pod_bundle` · **Supersedes:** PR #51 (`feat/pod-export-import`, closed as an experiment)

## Motivation

A pod — its tables, agents, functions, workflows, schedules, apps, and surfaces — should be shareable as a single artifact. Export a pod as a bundle, publish it to GitHub with a README and an install badge, and let anyone import it into their own workspace with an app-store-style install flow. The CLI already has a bundle format (`lemma pods export` / `lemma pods import`, format version 2); this design brings the same story to the API and UI without duplicating that format logic.

## Postmortem: why PR #51 was discarded

PR #51 built the full feature but modeled every operation as a synchronous API request handler:

1. **Held DB connections across long I/O.** The import apply loop, the GitHub publish stream (an NDJSON generator), zipball fetches, and AI README generation all ran inside request handlers with the request-scoped session/UoW alive for the entire duration — minutes for a large pod. FastAPI keeps yield-dependencies alive for a whole `StreamingResponse` body, so even the "streaming" publish pinned a connection. With `db_pool_size=10 + 10 overflow` per process, a handful of concurrent imports exhausts the pool and stalls every other request.
2. **A durable table for transient state.** A new `pod_imports` table (+ migration) persisted plan and per-step checkpoints, with a real commit per step *on the held session* — durable workflow state for what is fundamentally a progress cache.
3. **`/tmp` staging.** Bundles were extracted to local disk in the API process. The moment API and worker run as separate replicas (or the process restarts), staged state is gone.
4. **Heuristic concurrency control.** Concurrent applies were guarded by a 120-second `updated_at` staleness check.

The lessons, inverted, are this design.

## Design principles

1. **Long-running work runs as streaq jobs, never in request handlers.** Endpoints accept, enqueue, and return `202`. This is the platform's established pattern (`process_agent_run`, `process_datastore_file_task`).
2. **No new DB tables — import/publish state is ephemeral, in Redis, with a ~6h TTL.** The insight that makes this safe: **re-planning is the real resume mechanism.** The plan builder computes a *diff against the pod's current resources* (CREATE / UPDATE / SKIP), and apply steps are idempotent upserts. If Redis state is lost mid-import, the user re-uploads and re-plans; already-applied resources come back as UPDATE/SKIP and apply continues from reality. Redis state is a UI/progress cache, never a source of truth.
3. **Short-lived UoW scopes everywhere.** Jobs open a `SessionUnitOfWorkFactory` scope per phase or per step and release the connection before any staging, GitHub, or LLM I/O — the `FunctionUseCases` phase-split pattern. Nothing holds a pooled connection while bytes move.
4. **Bundle archives live in object storage** (`build_object_store()`), keyed `pod-imports/{import_id}/bundle.zip`. Replica-safe, worker-reachable, swept on the same TTL horizon. Redis holds state JSON only, never blobs.
5. **One bundle format, one implementation.** A new pure-Python package `lemma-pod-bundle` owns the format vocabulary (layout, manifest, table diff, `${var}` portability, JSONC); both the CLI and the backend import it, so they cannot drift.
6. **The CLI flow is untouched and remains the agent-facing default.** `lemma pods import <dir>` keeps doing client-side dry-run + per-resource upserts against the existing REST APIs — progressive, incremental, no server-side import records, no Redis entries. Agents iterating on a local pod folder never create import state on the server. The server-side engine exists for UI uploads and GitHub installs, where the client can't orchestrate.

## Architecture

```
        upload zip / GitHub repo URL
                │
   POST /pods/{id}/bundle/imports ──► stage archive in object store
                │                     write ImportState to Redis (queued)
                │                     enqueue streaq job ── 202 {import_id}
                ▼
   worker: plan_pod_import
     ├─ download + validate bundle              (no DB)
     ├─ snapshot existing pod resources         (one short UoW)
     ├─ pure diff → plan (CREATE/UPDATE/SKIP,   (no DB)
     │   destructive flags, ${var} specs)
     └─ Redis: awaiting_confirmation ──► SSE/poll: user reviews plan,
                │                         fills variables, confirms
                ▼
   POST …/apply ──► enqueue apply_pod_import (dedup id = concurrency guard)
                ▼
   worker: apply_pod_import
     └─ per step: short UoW (upsert via module service, commit)
                  → Redis checkpoint → publish progress event
     └─ final short UoW: append PodRecipe to pod config
     └─ Redis: completed; delete staged archive
```

### Redis state

- Keys: `pod-bundle:import:{id}`, `pod-bundle:export:{id}`, `pod-bundle:publish:{id}` — JSON documents via the existing `RedisJsonCache`, TTL 6h refreshed on every write. Expired key ⇒ API returns `410 {"code": "import_expired"}`; the fix is always "re-upload / re-run".
- **Single-writer discipline:** the API process writes the initial document (and a `cancelling` marker); after enqueue, the worker is the only writer. This is guaranteed by the streaq dedup job id (`pod-import:{import_id}` etc.) — a duplicate enqueue returns `None` and the API surfaces `409`. Read-modify-write on the state document is therefore race-free with no locks and no staleness heuristics.
- Every write bumps a monotonic `seq`, refreshes the TTL, then best-effort publishes an event to `pod-bundle:events:{id}` via `ChannelService`. Event delivery never gates job progress.

`ImportState` (abridged):

```json
{
  "import_id": "…", "pod_id": "…", "user_id": "…",
  "source": {"kind": "upload|github", "repo_url": null, "bundle_sha256": "…"},
  "status": "queued|fetching|planning|awaiting_confirmation|applying|completed|failed|cancelled",
  "staging_key": "pod-imports/{id}/bundle.zip",
  "plan": {"format_version": 2, "variables": [], "warnings": [],
           "steps": [{"index": 0, "kind": "table", "name": "leads", "action": "UPDATE",
                      "destructive": true, "detail": {"columns_to_remove": ["score"]},
                      "status": "pending|running|done|failed|skipped", "error": null}]},
  "progress": {"done": 3, "total": 12},
  "variables_provided": {}, "error": null, "seq": 17,
  "created_at": "…", "updated_at": "…", "completed_at": null
}
```

### Durable record: recipes in pod config

The only durable write is provenance, and it rides on an existing row: `PodConfig` gains a typed `recipes: list[PodRecipe]` field — `{kind: "upload"|"github", repo_url?, name, format_version, imported_at, imported_by}` — appended in a short UoW when an apply completes. Repeat installs append. (It must be a *typed* field: `update_pod`'s config merge and `PodConfig.from_raw` drop unknown keys.) The serializer omits the list when empty, so existing config blobs are byte-identical.

### Jobs

| Job | Dedup id | Phases |
|---|---|---|
| `plan_pod_import` | `pod-import-plan:{id}` | Redis `planning` → download/validate bundle (no DB; invalid = terminal, no retry) → snapshot pod resources (one short UoW) → pure diff → Redis `awaiting_confirmation` |
| `apply_pod_import` | `pod-import:{id}` | per step: skip if checkpointed `done` → short UoW upsert → Redis checkpoint + event; domain errors terminal, infra errors re-raise for streaq retry (checkpoints make retries cheap); final short UoW appends the recipe |
| `import_pod_github` | `pod-import-plan:{id}` | resolve Composio account (short UoW) → fetch zipball (no DB) → stage → fall through to plan routine |
| `export_pod_bundle` | `pod-export:{id}` | snapshot (short UoW) → assemble zip in worker scratch (no DB) → upload to object store → Redis `ready` |
| `publish_pod_github` | `pod-publish:{id}` | resolve account + snapshot (short UoW) → build bundle + README (+ optional AI polish; degrades, never fails) (no DB) → Composio: create repo, per-file uploads with chunk fallback, per-file Redis checkpoints → `completed` with `repo_url` |
| `sweep_pod_bundle_staging` | cron `*/30 * * * *` | delete staged objects whose state key is gone/terminal past TTL; mark states stuck in `planning|applying|publishing` beyond job-timeout+buffer as `failed` |

Apply-step idempotency is load-bearing: each applier **re-checks existence by name at apply time** (exists ⇒ update, else create), so a crash between a step's DB commit and its Redis checkpoint replays safely. Worker-side authorization uses the established no-`Request` pattern: `AuthorizationDataService.build_user_context(user_id, pod_id)` inside a short UoW, wrapped in `context_scope` — imports run *as the importing user*, so permission checks (POD_UPDATE etc.) hold in the worker exactly as they would in the API.

### Progress: SSE over Redis pub/sub, polling as the floor

`GET …/{id}/events` follows the existing conversation-stream shape (`stream_conversation`): subscribe to the Redis channel first, authorize in a short UoW, then stream `text/event-stream` **holding no DB connection**. On connect the endpoint emits one `snapshot` frame from the current Redis state (with its `seq`), then live frames; clients discard frames with `seq ≤` the snapshot's, so reconnects and late joins are always coherent. `GET …/{id}` (pure Redis read) is the polling fallback — no DB is touched to check progress, ever.

### API surface (mounted by the `pod_bundle` module; API_VERSION 3.2.0 → 3.3.0)

| Endpoint | Guard | Semantics |
|---|---|---|
| `POST /pods/{pod_id}/bundle/imports` | POD_UPDATE | multipart zip → stage → `202 {import_id}`; `413` oversize, `422` not-a-zip |
| `POST /pods/{pod_id}/bundle/imports/github` | POD_UPDATE | `{repo_url\|owner+repo, ref?, account_id?}` → `202` |
| `POST /pods/bundle/imports` | POD_CREATE | new-pod install: create empty pod (short UoW), then same pipeline |
| `GET /pods/{pod_id}/bundle/imports/{id}` | POD_READ | Redis-only read; `410` expired |
| `POST …/{id}/apply` | POD_UPDATE | `{variables, confirm_destructive, skip_steps?}`; `409` unless `awaiting_confirmation\|failed`; `422` unconfirmed destructive / missing vars; dedup-`None` ⇒ `409` already running |
| `POST …/{id}/replan` | POD_UPDATE | re-plan against staged bundle; `410` if swept |
| `DELETE …/{id}` | POD_UPDATE | abort jobs, delete state + staging |
| `GET …/{id}/events` | POD_READ | SSE: `snapshot`, `status`, `step`, `progress`, `completed`, `error`, `expired` |
| `POST /pods/{pod_id}/bundle/exports` | POD_READ | `{include?, with_data?}` → `202 {export_id}` |
| `GET …/exports/{id}` / `…/download` | POD_READ | status / `StreamingResponse` from object store (no DB during stream) |
| `POST /pods/{pod_id}/bundle/publishes` | POD_READ | `{repo_name, private, account_id, ai_readme}` → `202`; GitHub authority comes from the user's own connector account |
| `GET …/publishes/{id}` (+`/events`) | POD_READ | status / SSE |

### The shared library: `lemma-pod-bundle`

Everything format-shaped moves out of `lemma-cli/lemma_cli/cli_app/pod_bundle.py` into a top-level pure package (stdlib + pydantic only): `layout` (FORMAT_VERSION, resource dirs, manifest model), `jsonc`, `diff` (table column diff, FK ordering), `portability` (`${var}` extract/apply), `normalize` (payload transforms), `archive` (zip-slip-safe pack/unpack, size caps), `requirements`. The CLI keeps its SDK-facing export/import loops and re-imports the pure pieces — zero behavior change, proven by its existing test suite. The backend's plan builder, applier, and exporter consume the same modules. One `FORMAT_VERSION` definition, two consumers, no drift.

## What this design explicitly does not do

- **No CLI changes in behavior.** Progressive local-folder import (dry-run + per-resource upsert) stays the default for agents building pods; a `--server-side` CLI flag is out of scope.
- **No import history table.** If durable analytics are ever needed, terminal state events can feed telemetry; the recipe entries in pod config record what matters to the pod.
- **No direct GitHub API client.** All GitHub I/O goes through the Composio connector (same consent/account model as every other connector).

## Test strategy

- **Unit:** shared-lib format matrix (diff, FK order, `${var}` round-trip, zip-slip rejection); plan builder CREATE/UPDATE/SKIP + destructive matrix against fake snapshots; applier idempotency (apply twice; crash between commit and checkpoint); state store TTL/seq/expiry; publisher against a fake Composio client (repo-exists, chunk fallback); recipe round-trip through `PodConfig.from_raw` + `update_pod`; controller status-code semantics with faked queue/store.
- **E2E** (real ASGI app + the real streaq worker subprocess from `test_support/e2e_base.py`): upload→plan→apply roundtrip into an existing pod; update-in-place with destructive confirmation; new-pod install; resume after a failed step; expiry (410); GitHub import from a fixture zipball (Composio faked); publish (Composio faked; README + badge + repo_url); SSE snapshot-then-live coherence.
- **Pool-safety regression check:** during a large e2e apply, `pg_stat_activity` shows no long-held / idle-in-transaction connections — the failure mode this design exists to prevent.
