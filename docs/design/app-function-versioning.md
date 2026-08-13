# App releases and function revisions: history, preview, promote

Status: implemented.

Three things this delivers:

1. **Apps** — list every release of an app, preview any release without
   promoting it, and set an older release live.
2. **Functions** — list every built revision, run a specific revision without
   promoting it, and set an older revision live.
3. **Bundles** — an exported app always carries its source; it carries its build
   too, and an import reuses that build instead of rebuilding when the build is
   portable.

---

## 1. What exists today

### Apps — releases exist, history is already retained

`app_releases` rows are created on every bundle upload
(`AppService.finalize_upload_bundle`), keyed by `version` = sha256 of the dist
archive, with `dist_root_path` / `dist_archive_path` in app storage.
`apps.current_release_id` points at the live one. Nothing deletes old releases
or their bytes until the app itself is deleted.

**So rollback data already exists.** What is missing is the read side (no list
endpoint, no UI), the write side (no way to move `current_release_id`), and one
schema gap:

> **Source is app-level, not release-level.** `apps.source_archive_path` is a
> single column overwritten on every upload. The path is content-addressed
> (`source/<sha>/archive.zip`), so old source blobs survive in storage — but
> they are unreferenced. Roll the dist back today and the app row still points
> at the *newest* source, so an export after a rollback would ship new source
> next to an old build.

Serving: `AppAssetResolver.resolve` always reads `app.current_release_id`. The
public host is `<public_slug>.<app_base_domain>`; the slug reaches the backend as
the `X-App-Public-Slug` header, injected by the cloud nginx ingress or, locally,
by `AppHostRoutingMiddleware` which derives it from `Host`. The ETag is
`release.version` (plus a runtime-config token on entrypoints).

### Functions — revisions exist in storage, but are not indexed

`FunctionUseCases._apply_code` builds an immutable artifact, writes it to
`artifacts/<hex>.zip`, writes the source to `revisions/<hex>/function.py`, and
sets `functions.revision_hash` / `code_path` / the three schema columns. Both
storage paths are content-addressed and **never deleted**, so the bytes of every
revision a function ever had are still there.

There is no `function_revisions` table. The only trace of a past revision is the
`revision_hash` column on `function_runs`.

Execution already carries the revision: `resolve_execute` stamps
`function.revision_hash` onto the run row, and `FunctionRuntimeGateway` derives
`artifacts/<hash>.zip` from **the run's** hash, verifying the digest before use.

> **So running a pinned revision needs no runtime change at all** — only a run
> row created with a different hash.

### Export / import — source *or* build, never both

`Exporter._export_app_assets` (backend) and `_download_app_assets` (CLI) both do
the same thing: try the source archive, extract it to `source/` and **return**;
only if there is no source do they write `dist.zip`. So a bundle never carries
both.

Which apps have no source?

| Path | Source uploaded? |
|---|---|
| CLI `lemma apps deploy` (vite) | yes — project tree |
| CLI `lemma apps deploy` (html / static) | yes — the dist doubles as source |
| `create_app_from_widget` (save widget as app) | **no** — dist only |
| Bundle import of a `dist.zip`-only app (`AppStepRunner._artifacts`) | **no** — propagates the gap |

So the reported "we export the build, not the code" is the widget-promotion path
and anything that has round-tripped through a bundle since.

**The rebuild-on-import premise is stale.** `AppStepRunner` rebuilds every vite
app in a sandbox because "a Vite app bakes `VITE_LEMMA_POD_ID` into its bundle".
The scaffolded client is `new LemmaClient()` with no overrides, and
`resolveConfig` prefers `window.__LEMMA_CONFIG__` (injected at serve time) over
`import.meta.env`. A dist built from the template runs unchanged on any pod. Only
an app that reads `import.meta.env.VITE_LEMMA_POD_ID` in its own code is
genuinely non-portable — and that is detectable (§6).

---

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | Preview is **host-based**: `<slug>--r<N>.<app_base_domain>` | Vite builds use `base: '/'`, so assets are absolute (`/assets/…`). Under a path prefix they would resolve against the live release. A host carries the release through every asset request. `normalize_public_slug` collapses runs of `-`, so a real slug can never contain `--` and the label parses unambiguously. The CORS regex `([a-z0-9-]+\.)?<base>` already matches. |
| D2 | A preview **inherits the app's visibility** | The shell is HTML and JS; every data call inside it is authorized on its own (this is already the documented model for the public host). A stricter signed-preview-link variant is described in §4 if we want it. |
| D3 | Releases get a **per-app sequential `release_number`** | A sha256 is 64 hex chars — too long for a DNS label (63 max) and unreadable in a UI. `v7` is both. The digest stays as the identity and the ETag. |
| D4 | Release identity stays **the dist digest** | Keeps the existing dedup and unique constraint. Consequence: two different sources that compile to byte-identical dist collapse into one release, and a re-upload updates that release's source pointer. Acceptable — identical served bytes are not a new release. |
| D5 | Promoting a release **also restores that release's source pointer** | Otherwise a rollback leaves the app exporting new source with an old build. |
| D6 | Function revisions get **their own table**, written in the existing persist phase | Needs `created_at` / author / schema snapshot, none of which storage has. The write goes in `persist_create` / `persist_update`, both already inside a short UoW — no new transaction, no connection held across the sandbox. |
| D7 | Pinned-revision runs go through **the existing run endpoint** with an optional `revision` field, gated on `FUNCTION_UPDATE` | One code path; the dispatcher already resolves the artifact from the run's hash. Running a *superseded* build is an editor action, not an executor one. |
| D8 | Promoting a revision **restores its schemas too** | `input_schema` / `output_schema` / `config_schema` live on the function row, and agents and workflows read them. Promoting without them would leave the contract lying about the code. |
| D9 | Export carries **both** `source/` and `dist.zip`; import reuses the dist when portable | What the request asks for, and it removes the sandbox dependency from most imports. |
| D10 | Retention keeps: the live version always, plus a **floor** of the 10 newest at any age, plus anything under 30 days up to a **ceiling** of 20 | Two knobs are not enough. The floor keeps a dormant app rollback-able the day a bad deploy lands; the ceiling is what bounds a burst — "keep anything recent" has no upper limit on its own. |
| D11 | Pruning runs **inline after a deploy**, with a daily BULK-lane cron as the age backstop | A deploy is when storage grows, so that is when surplus is worth removing — no scheduled work needed for the common case. The cron catches only what inline pruning structurally cannot: a resource that stops being deployed. |
| D12 | Pruned rows are **tombstoned** (`pruned_at`), not deleted | History that silently skips v3 is worse than history that says the build was removed, and old function runs still resolve the revision they executed. |

### Constraints the implementation must respect

- **`make architecture`** ratchets per-file line counts. `app_service.py` is 599
  (baseline 661) and `function_service.py` is 616 (baseline 728) — thin headroom.
  New logic goes in **new files**, not into those two. Broad-catch counts per
  module are frozen: no bare `except Exception` in new code.
- **Route inventory**: the `Apps` and `Functions` tags are already mapped in
  `generate_route_inventory.py`, so no new tag is needed. Run
  `make route-inventory` and `dump_openapi_spec.py --check`.
- **Every new operation needs a hand-written Python SDK facade** plus a TS
  namespace method — generated clients alone do not satisfy the gate.
- **All nine component versions must stay equal**; no bump is required per PR.
- **Frontend vitest `include` is literal** — a new test file under
  `components/` is silently skipped unless the pattern covers it.

---

## 3. Phase 1 — App releases become first-class

**Migration** `2026-08-13_app_release_history_0017.py` (revises
`0016_unique_surface_email`):

```
app_releases:
  + release_number      int          not null          # per-app, 1-based
  + source_archive_path varchar      null
  + source_digest       varchar      null
  + created_by          uuid         null  FK users.id ON DELETE SET NULL
  + label               varchar      null              # optional human note
  + unique (app_id, release_number)
```

Backfill: number existing rows per app by `created_at` ascending; copy
`apps.source_archive_path` onto that app's newest release only (it is the only
release we can honestly attribute it to). Downgrade drops the columns.

**New file** `app/modules/apps/services/app_release_service.py`:

- `list_releases(pod_id, app_name, ctx)` — `APP_READ`; returns releases newest
  first with an `is_live` flag, size, and whether source is attached.
- `promote_release(pod_id, app_name, ref, ctx)` — `APP_UPDATE`; resolves `ref`
  (release number or digest prefix), sets `apps.current_release_id` and mirrors
  the release's `source_archive_path` / status onto the app row (D5). A
  promotion is a **pointer move**: no new release row, no new bytes.
- `resolve_release(app, ref)` — shared by promote and by preview serving.

**`AppAssetResolver`**: extract the release lookup so `resolve()` takes an
already-resolved release instead of always reading `current_release_id`. Preview
passes the requested release and a `public_url` pointing at the **preview** host,
so branding and social metadata do not claim to be the live app.

**`finalize_upload_bundle`**: allocate `release_number`
(`MAX(release_number)+1` per app, retried on the unique constraint), and record
`source_archive_path` / `source_digest` / `created_by` on the release row.

---

## 4. Phase 2 — Preview URLs

`app_slug_from_host` returns `(slug, release_ref | None)`, splitting the
left-most label on the last `--`. `AppHostRoutingMiddleware` sets
`X-App-Public-Slug` and, when present, `X-App-Release`.
`public_app_controller` passes the release ref through to
`serve_public_asset`, which resolves that release instead of the current one and
refuses when the app is not `PUBLIC` — exactly the rule the live path already
applies.

```
live     https://orders.apps.lemma.work
preview  https://orders--r7.apps.lemma.work        (also accepts --<digest-prefix>)
```

The ETag already keys off the release digest, so a preview and the live app
never share a cache entry.

**External dependency**: the cloud nginx ingress (`app_ingress.yaml`, not in this
repo) derives the slug from the host. It must either forward the whole label and
let the middleware split it, or learn the same split. **This needs confirming
before the preview host works in cloud** — locally the middleware handles it.

**A POD-visibility app cannot be previewed** — but it cannot be *viewed* today
either (the anonymous host route refuses anything not `PUBLIC`, which is exactly
why apps default to `PUBLIC`). Consistent, and worth stating in the UI.

**Hardening option (not recommended for v1)**: gate previews behind a short-lived
signed token — `POST .../releases/{ref}/preview-link` returns
`https://orders--r7.…/?__lemma_preview=<jwt>`, the preview host sets a
host-scoped cookie and refuses without it. Costs a token issuer, a cookie, and a
second auth path on the hottest route in the system. Only worth it if an
unpromoted build is considered confidential.

---

## 5. Phase 3 — Function revisions

**Migration** `..._function_revisions_0018.py`:

```
function_revisions:
  id               uuid pk
  function_id      uuid  FK functions.id ON DELETE CASCADE
  revision_number  int   not null
  revision_hash    varchar(71) not null
  code_path        varchar not null
  input_schema     jsonb not null
  output_schema    jsonb not null
  config_schema    jsonb null
  created_by       uuid null FK users.id ON DELETE SET NULL
  label            varchar null
  created_at       timestamptz not null
  unique (function_id, revision_hash)
  unique (function_id, revision_number)
  index (function_id, created_at desc)
```

Backfill: one row per function with a non-null `revision_hash`, snapshotting the
current `code_path` and schemas, `created_at = functions.updated_at`.

> Older hashes seen in `function_runs` are deliberately **not** backfilled: their
> artifacts and source still exist in storage, but their schemas are
> unrecoverable, so a synthesized row would be a guess. Those runs keep showing
> their hash; only revisions built from here on get full history.

**New file** `app/modules/function/services/function_revision_service.py` +
repository methods (keeps `function_service.py` off the ratchet):

- `record_revision(...)` — idempotent insert on `(function_id, revision_hash)`,
  called from `persist_create` / `persist_update` inside their existing UoW.
- `list_revisions(pod_id, name, ctx)` — `FUNCTION_READ`; newest first,
  `is_live` flag, no code body.
- `get_revision(pod_id, name, ref, ctx)` — includes the code read from
  `code_path` (storage read, outside the UoW).
- `promote_revision(pod_id, name, ref, ctx)` — `FUNCTION_UPDATE`; verifies the
  artifact object still exists, then copies `revision_hash`, `code_path` and all
  three schemas onto the function row and sets `status = READY` (D8). Response
  reports whether the schemas differ from the previously-live ones so the UI can
  warn that agents and workflows bound to this function may break.

**Running a revision without promoting** (D7): add
`revision: str | None` to `ExecuteFunctionRequest` (it is `extra="forbid"`, so
this is a purely additive optional field). `resolve_execute` resolves it to a
hash, requires `FUNCTION_UPDATE` when it is not the live one, and stamps it onto
the run. Nothing downstream changes — the dispatcher and gateway already work
from the run's hash and verify the digest.

---

## 6. Phase 4 — Bundles carry source *and* build

1. **`create_app_from_widget`** uploads the wrapped document as **both**
   `source_archive_bytes` and `dist_archive_bytes`. Widget-promoted apps stop
   being source-less at the origin. *(One-line fix, largest single win.)*
2. **`AppStepRunner._artifacts`**: when a bundle carries only `dist.zip`, use
   those bytes as the source too. `classify_source_dir` already reads an
   extracted dist as `"static"`, so this needs no new tier.
3. **Exporters** (`Exporter._export_app_assets` **and** the CLI's
   `_download_app_assets` — they must stay mirrors): stop returning early after
   source. Export `source/` *and* `dist.zip`, each budget-checked independently,
   dist dropped first when over budget. Warn in the export report when an app has
   no source at all.
4. **App manifest** gains a `dist` block:
   `{"release_number": 7, "digest": "…", "portable": true}`. `portable` is
   computed by scanning the dist bytes for the source pod's UUID string — cheap,
   deterministic, and it catches exactly the apps that bake pod identity in.
5. **Importer**: if `dist.zip` is present and (`portable` **or** the source is
   `static`/`html`) → deploy it directly and skip the sandbox build. Otherwise
   rebuild as today. No dist → build, as today. Most imports stop needing a
   sandbox and drop from minutes to seconds.
6. `lemma_pod_bundle` needs no layout change — nothing rejects a directory that
   has both `source/` and `dist.zip`.

---

## 7. Phase 5 — API surface

All under existing tags, so the route inventory needs no new mapping.

**Apps**

| Method | Path | Operation |
|---|---|---|
| GET | `/pods/{pod_id}/apps/{app_name}/releases` | `app.release.list` |
| POST | `/pods/{pod_id}/apps/{app_name}/releases/{release_ref}/promote` | `app.release.promote` |
| GET | `/pods/{pod_id}/apps/{app_name}/releases/{release_ref}/source/archive` | `app.release.source.archive.get` |
| GET | `/pods/{pod_id}/apps/{app_name}/releases/{release_ref}/dist/archive` | `app.release.dist.archive.get` |

There is deliberately **no** authed per-release *asset-serving* route: absolute
`/assets/…` URLs cannot resolve under a path prefix (D1), so preview is the
host, and these paths only serve whole archives.

**Functions**

| Method | Path | Operation |
|---|---|---|
| GET | `/pods/{pod_id}/functions/{name}/revisions` | `function.revision.list` |
| GET | `/pods/{pod_id}/functions/{name}/revisions/{ref}` | `function.revision.get` |
| POST | `/pods/{pod_id}/functions/{name}/revisions/{ref}/promote` | `function.revision.promote` |

Plus `revision` on the existing `function.run` body.

Each one needs: the hand-written Python facade (`resources/apps.py`,
`resources/functions.py`), the TS namespace method
(`namespaces/apps.ts`, `namespaces/functions.ts`), a regenerated
`openapi_spec.json`, and a regenerated route inventory.

---

## 8. Phase 6 — UI

**Apps.** There is no app detail page today — only the list at
`/pod/[id]/app/pages` and the iframe at `/pod/[id]/app/view`. Add a **Versions**
drawer opened from the `AppFrame` header (beside Reload / Copy / Share):

- a row per release: `v7`, short digest, relative time, author, `LIVE` badge,
  "source attached" marker;
- **Preview** — swaps the iframe `src` to the preview host and shows a
  persistent "Previewing v5 — not live" banner with *Set live* and *Back to
  live*;
- **Set live** — confirm dialog naming both versions; on success invalidate the
  app index query and reload the frame;
- **Download** source / build per release.

**Functions.** Add a **Revisions** tab to `FunctionTestPanel` beside Test and
Runs:

- a row per revision: `r12`, short hash, relative time, author, `LIVE` badge;
- **View code** — read-only editor; a diff against the live revision is a
  nice-to-have, not v1;
- **Run this revision** — loads the input form and submits with `revision` set;
  the resulting run is tagged with the revision it used;
- **Set live** — confirm dialog that surfaces the schema-diff warning from the
  promote response;
- the Runs list gets a revision chip so a failing run is attributable.

---

## 9. Testing

**Backend unit** — release numbering and its retry; `promote_release` moves the
pointer and the source pointer together; `app_slug_from_host` splits
`slug--r7`, rejects `slug--`, `--r7`, and multi-dot labels; preview refuses a
non-`PUBLIC` app; `record_revision` is idempotent under replay; `promote_revision`
restores schemas; a pinned run without `FUNCTION_UPDATE` is refused.

**Backend e2e** — upload two app bundles, promote the first, assert the bytes
served on the live host change and the preview host serves each release
independently; update a function twice, promote the first revision, assert the
run executes the older artifact. *(Note: backend e2e is not a required check —
a red e2e can ship. Read the output, don't just trust the merge.)*

**pod_bundle e2e** — extend the `with_app_static` fixture and add a
source-and-dist fixture; assert an export→import round-trip preserves source,
reuses a portable dist without touching the sandbox, and rebuilds a
non-portable one.

**CLI** — `_download_app_assets` writes both directories; `deploy_app_bundle` is
unchanged.

**Frontend** — check `vitest.config` `include` actually covers the new test
files before writing them.

---

## 10. Sequencing

Phases 1→2→5(apps)→8(apps) and 3→5(functions)→8(functions) are independent of
each other and can land as separate PRs. **Phase 6 (bundles) is independent of
both and is the smallest, highest-value change** — items 1 and 2 alone are a
handful of lines and stop the source loss at its source. Recommended order:

1. Phase 6 items 1–2 (stop losing source) — small, shippable alone.
2. Phase 1 + 2 + apps API/UI (app releases end to end).
3. Phase 3 + functions API/UI (function revisions end to end).
4. Phase 6 items 3–5 (export both, reuse portable builds).

---

## 11. Open questions

1. **Preview visibility** — inherit the app's visibility (D2, recommended), or
   signed preview links (§4)?
2. **Retention** — nothing prunes releases or artifacts today. Keep everything,
   or cap at the last N per app/function? Storage grows with every deploy.
3. **Rollback and source** — confirm D5: after rolling an app back to v5, an
   export ships v5's source, not the newest.
4. **Function revision backfill** — current revision only (recommended), or also
   synthesize rows for hashes seen in `function_runs` (schemas unrecoverable)?
5. **Cloud ingress** — who confirms `app_ingress.yaml` forwards the full
   `<slug>--r<N>` label?

---

## 12. Retention (implemented)

Nothing had ever deleted a release or a revision, so storage grew with every
deploy forever.

**The rule** lives in one pure function, `app/core/retention.py`, so both modules
apply it identically and it can be argued with in one test file. Rank a
resource's versions by `created_at` descending (rank 1 = newest):

```
KEEP   if it is live                              — always, no exceptions
KEEP   if rank <= keep_last                       — floor: rollback always works
KEEP   if age < keep_days AND rank <= max_keep    — young, up to the ceiling
PRUNE  otherwise
```

Defaults: `keep_last=10`, `keep_days=30`, `max_keep=20`, all configurable
(`AppsSettings.app_release_*`, `settings.function_revision_*`). Ties in
`created_at` break on `id`, which is load-bearing rather than arbitrary: both
tables key on uuid7, so id order *is* creation order, and two builds recorded in
the same tick can never rank a newer one below an older one.

**Where it runs.** Inline after a deploy or a code save commits — best-effort, so
a prune failure can never fail the deploy — plus a daily BULK-lane cron
(`sweep_app_releases`, `sweep_function_revisions`) for resources that stopped
being deployed. Both follow the pool discipline: a short unit of work selects the
versions and stamps `pruned_at`, then the object deletes run with no pooled
connection held. Stamping first means a sweep that dies midway leaves rows that
say "build removed" rather than rows that still offer to promote.

**Safety rails**, each with its own test:

- The live release/revision is exempt at any age or rank.
- A sweep never issues `delete_prefix("")` — on a bucket-root store that would
  take the bucket.
- **Shared source blobs**: app source is content-addressed, so a dist-only change
  produces a new release pointing at the *same* `source/<sha>/archive.zip`.
  A source blob is deleted only when no retained release (and not the app row)
  still references it.
- **In-flight runs**: a function revision with a `PENDING`/`RUNNING` run is never
  pruned, and neither is one younger than the longest execution deadline — a run
  is created and dispatched in separate steps, so a just-recorded revision can be
  pinned by a run that does not exist in the table yet.

**Two prerequisites this uncovered.** Function storage had no deletion path at
all (`FunctionFileManager` was read/write only), which also meant
`delete_function` had been orphaning every artifact it ever built —
fixed alongside. And `AppFileManager.delete_prefix` iterated obstore's *sync*
list inside `async def`, blocking the event loop per page against a cloud store;
tolerable when it only ran on app deletion, not once retention walks release
prefixes routinely. Both now use `list_async`.
