# App releases and function revisions

Status: implemented. This records the decisions that outlive the change — the
ones a future reader would otherwise have to reverse-engineer from the code. The
phase-by-phase build plan that used to live here has been removed: it described
work that is done, and the mechanisms are documented where they run
(`app/core/retention.py`, `apps/api/host_routing.py`, and the two migrations).

Operator-facing settings are in [configuration.md](../configuration.md#build-retention).

## Decisions

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

- **`make architecture`** ratchets per-file line counts, and the headroom is
  thin: `app_service.py` sits just under its 661 baseline, and
  `function/infrastructure/repositories.py` had to have its revision methods
  split into `revision_repository.py` once main and this branch together pushed
  it past the 600 ceiling. New logic goes in **new files**. Broad-catch counts
  per module are frozen too: no bare `except Exception` in new code.
- **Route inventory**: the `Apps` and `Functions` tags are already mapped in
  `generate_route_inventory.py`, so no new tag is needed. Run
  `make route-inventory` and `dump_openapi_spec.py --check`.
- **Every new operation needs a hand-written Python SDK facade** plus a TS
  namespace method — generated clients alone do not satisfy the gate.
- **All nine component versions must stay equal**; no bump is required per PR.
- **Frontend vitest `include` is literal** — a new test file under
  `components/` is silently skipped unless the pattern covers it.

---


| D13 | Recording a release is an **upsert on the dist digest**, not an insert | `uq_app_release_version` forbids a second row per digest, and two rows would point at the same content-addressed prefix, so pruning either would delete the other's bytes. Redeploying a build retention had already pruned therefore has to revive that release — clearing `pruned_at` and rewriting the bytes — rather than mint a new one. It also makes concurrent uploads of one digest safe: the loser gets the winner's row instead of deleting the winner's bytes in its cleanup. |
| D14 | Version numbers are allocated **inside the INSERT, under a lock on the parent row** | A read-then-write lets two racing uploads pick the same number; an in-INSERT `max+1` narrows the window but does not close it under READ COMMITTED. The lock costs nothing because the same unit of work updates that row a few statements later anyway. |
| D15 | The retention sweep pages over a **candidate filter, not a cursor column** | The candidate set drains — a pruned version leaves it permanently — so it needs no "when did I last look at you" stamp, unlike a sweep over rows that stay eligible forever. What it does need is a filter carrying all three knobs, because `count(*) > keep_last` alone returns apps that have surplus but nothing prunable, which would starve the tail behind them forever. |
| D16 | A release rides in the **slug label**, with no separate release header | Nothing upstream ever set `X-App-Release`, so a client could supply one and pin the canonical live host to a superseded build. The cloud ingress already forwards the whole `orders--r7` label and the controller already had to split it; making that the only mechanism leaves nothing to forge. |

### Constraints the implementation must respect

- **`make architecture`** ratchets per-file line counts, and the headroom is
  thin: `app_service.py` sits just under its 661 baseline, and
  `function/infrastructure/repositories.py` had to have its revision methods
  split into `revision_repository.py` once main and this branch together pushed
  it past the 600 ceiling. New logic goes in **new files**. Broad-catch counts
  per module are frozen too: no bare `except Exception` in new code.
- **Route inventory**: the `Apps` and `Functions` tags are already mapped in
  `generate_route_inventory.py`, so no new tag is needed. Run
  `make route-inventory` and `dump_openapi_spec.py --check`.
- **Every new operation needs a hand-written Python SDK facade** plus a TS
  namespace method — generated clients alone do not satisfy the gate.
- **All nine component versions must stay equal**; no bump is required per PR.
- **Frontend vitest `include` is literal** — a new test file under
  `components/` is silently skipped unless the pattern covers it.

---
