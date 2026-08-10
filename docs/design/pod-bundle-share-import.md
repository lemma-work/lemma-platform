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

### Apps and connectors: the two steps that carry more than a manifest

Most resources are a single JSON manifest applied in one short UoW. **Apps** and **surfaces** (connectors) are not, and the exporter/applier handle each specially.

- **App source travels, not just metadata.** The exporter downloads each app's stored **source** archive into `apps/<name>/source/` (or, for a widget/no-source app, its built `dist.zip`) — the app code is the most important part of a pod, so a metadata-only export is useless. Byte-budgeted like other file/asset bytes.
- **Apps are rebuilt on import, in the importing user's sandbox.** A Vite app bakes `VITE_LEMMA_POD_ID` (and the API URLs) at build time and its `public_slug` is unique platform-wide — both change in the target pod — so a source-pod dist can't be reused. `AppStepRunner` (in `infrastructure/app_builder.py`) creates the app with a **deterministic** unique slug (idempotent on replay), then `AppSandboxBuilder` runs `npm install && npm run build` in the sandbox with the *target* pod's values and uploads the fresh dist. A **static** (no-`package.json`) app needs no build — the host injects `window.__LEMMA_CONFIG__` at serve time — so its files deploy as-is. Because the build takes minutes and touches the sandbox + object storage, the APP step is the one step that **cannot** run under the apply loop's per-step `uow_scope`: the loop special-cases `StepKind.APP` and hands the runner the `uow_factory`, which opens its own short UoWs around the connectionless build (create → build → resolve/write/finalize, mirroring `AppUseCases.upload_bundle`). Idempotency: create is by-name, the dist upload dedups on `sha256(dist)`, and a fully-deployed app (READY + current release) short-circuits on replay. A build failure is a terminal `AppBuildFailedError` (retrying identically fails the same way); transient sandbox churn re-raises for streaq to retry the whole (idempotent) job.
- **Connectors carry a required account.** The exporter tokenizes a surface's `account_id` into a `${..._account}` variable (the source org's account id is meaningless elsewhere). The plan marks account variables **required**, so `apply` (`422`) rejects an import that omits them — a connector is never silently imported unbound. `_apply_surface` then upserts the surface for the platform, binding the importer-supplied account, reusing the `agent.surface.upsert` controller's config helpers.
- **App slug as a variable.** The app `public_slug` is likewise tokenized into an optional `${<app>_slug}` variable (default = the original slug); the runner uses it when supplied and otherwise falls back to the app name, always allocating a platform-unique slug.

### Progress: SSE over Redis pub/sub, polling as the floor

`GET …/{id}/events` follows the existing conversation-stream shape (`stream_conversation`): subscribe to the Redis channel first, authorize in a short UoW, then stream `text/event-stream` **holding no DB connection**. On connect the endpoint emits one `snapshot` frame from the current Redis state (with its `seq`), then live frames; clients discard frames with `seq ≤` the snapshot's, so reconnects and late joins are always coherent. `GET …/{id}` (pure Redis read) is the polling fallback — no DB is touched to check progress, ever.

### API surface — URL-based (mounted by the `pod_bundle` module)

The orchestration API carries **no zip bytes**. Import takes a URL; export returns a signed, authenticated download URL. Bytes move only through the workers and two dumb byte endpoints (the uploads primitive and the download stream), neither holding a pooled connection. `kind` is the CAPS enum `BundleSourceKind` (`URL` | `GITHUB`).

**Why the download URL is backend-served, not a raw bucket URL:** it must be gated to *authenticated lemma users*, and a raw GCS/LocalStore URL carries only a signature — it can't check lemma auth. So the download is an endpoint that requires `CurrentUser` (any logged-in user, not pod-scoped) **and** verifies a signed token (stateless, HMAC via the `app/core/crypto` signer, purpose `pod-bundle-download-url`, embeds `(kind, id, expiry)`). Because the token names a staged object, the import worker verifies it and reads the object **straight from object storage** — no server-side HTTP fetch, so `kind=URL` has **no SSRF surface** (lemma-origin only).

| Endpoint | Guard | Semantics |
|---|---|---|
| `POST /pods/{pod_id}/bundle/imports` | POD_UPDATE | `{kind, url?, owner?, repo?, ref?, account_id?}` → `202 {import_id}`. `URL`: verify the lemma signed download token (`410` bad/expired, `422` non-lemma URL) → `import_pod_url` job. `GITHUB`: repo → `import_pod_github` job (`account_id` for private). |
| `POST /pods/{pod_id}/bundle/uploads` | POD_UPDATE | multipart `.zip` → stage → `201 {url, expires_at}` (a signed lemma URL to pass as `kind=URL`); `413` oversize, `422` not-a-zip. The only multipart endpoint. |
| `GET /pods/{pod_id}/bundle/imports/{id}` | POD_READ | Redis-only read; `410` expired |
| `POST …/{id}/apply` | POD_UPDATE | `{variables, confirm_destructive}`; `409` unless `awaiting_confirmation\|failed`; `422` unconfirmed destructive / missing vars; dedup-`None` ⇒ `409` |
| `POST …/{id}/replan` | POD_UPDATE | re-plan against staged bundle; `410` if swept |
| `DELETE …/{id}` | POD_UPDATE | abort jobs, delete state + staging |
| `GET …/{id}/events` | POD_READ | SSE: `snapshot`, `status`, `step`, `progress`, `completed`, `error`, `expired` |
| `POST /pods/{pod_id}/bundle/exports` | POD_READ | `{include?, with_data?, ttl_seconds?}` → `202 {export_id}` (ttl clamped to max) |
| `GET /pods/{pod_id}/bundle/exports/{id}` | POD_READ | status; when READY: signed `download_url`, `expires_at`, and `warnings` (data-cap notices) |
| `GET /pods/bundle/download?token=…` | CurrentUser (any user) + token | stream the archive (application/zip); not pod-scoped; `410` bad/expired token or swept archive |
| `POST /pods/{pod_id}/bundle/publishes` | POD_READ | `{repo_name, private, account_id, ai_readme}` → `202`; GitHub authority comes from the user's own connector account |
| `GET …/publishes/{id}` (+`/events`) | POD_READ | status / SSE |

**Export data limits (never dump GBs):** the schema always exports in full; row data + file/asset bytes are bounded best-effort with warnings — per-table record cap, overall record cap (later tables go schema-only), per-file byte cap, and total-file-bytes budget, all config-driven.

**Retention:** exports (and their download URLs) live to the export TTL (config, default 24h, capped ~7d) via a longer state TTL; imports stay ~6h. The sweep cron reclaims an archive once its state has expired.

### The shared library: `lemma-pod-bundle`

Everything format-shaped moves out of `lemma-cli/lemma_cli/cli_app/pod_bundle.py` into a top-level pure package (stdlib + pydantic only): `layout` (FORMAT_VERSION, resource dirs, manifest model), `jsonc`, `diff` (table column diff, FK ordering), `portability` (`${var}` extract/apply), `normalize` (payload transforms), `archive` (zip-slip-safe pack/unpack, size caps), `requirements`. The CLI keeps its SDK-facing export/import loops and re-imports the pure pieces — zero behavior change, proven by its existing test suite. The backend's plan builder, applier, and exporter consume the same modules. One `FORMAT_VERSION` definition, two consumers, no drift.

## What this design explicitly does not do

- **No CLI changes in behavior.** Progressive local-folder import (dry-run + per-resource upsert) stays the default for agents building pods; a `--server-side` CLI flag is out of scope.
- **No import history table.** If durable analytics are ever needed, terminal state events can feed telemetry; the recipe entries in pod config record what matters to the pod.
- **No direct GitHub API client.** All GitHub I/O goes through the Composio connector (same consent/account model as every other connector).

## Test strategy

- **Unit:** shared-lib format matrix (diff, FK order, `${var}` round-trip incl. the app-slug token, zip-slip rejection); plan builder CREATE/UPDATE/SKIP + destructive matrix + required-account classification against fake snapshots; applier idempotency (apply twice; crash between commit and checkpoint) + `_apply_surface` account binding against a fake surface service; app builder against a fake sandbox session (env injection with the target `VITE_LEMMA_POD_ID`, build-failure = terminal, tier selection: vite→build, static→deploy-as-is, dist-only fallback) and deterministic slug allocation; exporter app-asset download + byte-budget; state store TTL/seq/expiry; publisher against a fake Composio client (repo-exists, chunk fallback); recipe round-trip through `PodConfig.from_raw` + `update_pod`; controller status-code semantics with faked queue/store.
- **E2E** (real ASGI app + the real streaq worker subprocess from `test_support/e2e_base.py`): upload→plan→apply roundtrip into an existing pod; update-in-place with destructive confirmation; new-pod install; resume after a failed step; expiry (410); **static app import** (create → source/dist upload → release finalize → serves its `index.html`); **app-source export** round-trip (uploaded source comes back under `apps/<name>/source/` with the slug tokenized in `pod.json`); **connector import** (the plan surfaces the account as a required variable and `apply` without it is rejected `422`); GitHub import from a fixture zipball (Composio faked); publish (Composio faked; README + badge + repo_url); SSE snapshot-then-live coherence. The Vite build-in-sandbox path is unit-covered with a mocked session; a real `npm run build` in the Docker sandbox is gated behind `E2E_REAL=1`.
- **Pool-safety regression check:** during a large e2e apply, `pg_stat_activity` shows no long-held / idle-in-transaction connections — the failure mode this design exists to prevent.

## Executable-import hardening (grants, toolsets, durability)

The first slice made resources *appear*; this hardening makes them *work* — an
imported function can reach its tables, an imported agent can use its tools, and
every step is durably committed.

- **Function + agent grants apply on import.** A resource manifest may carry
  `permissions.grants` (`resource_type` / `resource_name` / `permission_ids`).
  The applier validates, normalizes (name → id), and replaces the grantee's
  resource grants in the step's own UoW — the same inline-grants path the
  function/agent controllers use — so an imported function's datastore reads and
  writes are authorized immediately. Function grants apply inline with the create
  (then the delegated-token env cache is dropped); agent grants apply in the
  deferred `AGENT_GRANTS` step, after every resource they reference exists.
- **Agent toolsets travel with the agent.** The manifest's `toolsets` are applied
  on create/update; without them a granted agent still can't act.
- **Every apply step commits.** The bare per-step UoW rolls back on error but does
  **not** auto-commit on success, so a step whose service didn't commit
  internally (e.g. workflow create) was silently lost — and a DONE checkpoint on
  lost data breaks crash-resume. The apply loop now commits each step before
  checkpointing it. Two applier bugs surfaced by the executable e2e were fixed
  alongside: `_apply_function`'s update path built the wrong `update_function`
  call, and `_flow_exists` treated a `None` "not found" as "exists" and skipped
  workflow creation.
- **Real use-case bundle fixtures + execution e2e.** Three hand-authored bundles
  live in the module (`tests/e2e/bundles/`): **support_inbox** (tickets table +
  `triage_ticket` function + `support_agent`), **lead_scoring** (leads table +
  `score_lead` function + `score_flow` workflow), and **moderation_queue**
  (submissions table + `flag_content` function + `reviewer_agent` +
  `screen_flow` workflow). Each e2e imports the bundle and then *runs* the
  imported function against the real datastore — proving the grants were applied,
  since without them the write returns a real 403 — and asserts the agent's
  toolsets/grants and the workflow's function cross-reference survived the import.
  A gated `real_llm` variant points `support_agent` at the real `system:lemma`
  runtime and asserts it files a ticket by calling the imported function tool
  (skipped without provider creds).
- **Not covered here:** schedule *firing* needs the full scheduler stack (a live
  scheduler API the session-scoped e2e worker can't reach), so schedule import is
  covered at the applier unit level and firing by the schedule module's own
  full-stack e2e.
