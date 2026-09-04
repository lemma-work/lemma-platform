# App releases and function revisions

App versions are deployments; function revisions identify compiled code. Operators
configure bounded build and source retention in [Configuration](../configuration.md#build-retention).

## Identity and storage

Each app upload receives its own release number and storage generation, even
when the dist digest matches an earlier upload. Source belongs to that deployment.
A rollback restores its source pointer; a legacy release with unknown source
must not export another release's source.

Function artifacts remain content-addressed for digest verification and runtime
caching. Physical artifact and source paths also contain an upload generation.
Saving unchanged retained code reuses its revision. Saving code that has expired
creates a new numbered revision with fresh storage paths; it does not revive the
old tombstone. Unused uploads from successful deduplication are discarded.
Schema inspection passes the upload generation to the runtime before activation;
ordinary runs resolve the retained generation from their immutable code digest.

## Retention and concurrency

Retention preserves the configured newest revisions and age window, subject to
the configured maximum. The live version and function revisions needed by
pending or running executions are protected. These protections can temporarily
leave more versions than the configured maximum.

Planning, promotion, and function run creation lock and reread the same parent
row within short database transactions. Pruning marks the selected revisions
before releasing that lock. A competing promotion or pinned execution therefore
either wins before pruning and protects its revision, or sees the tombstone and
is refused. Upload finalization also serializes on the parent row.

Storage deletion runs after the transaction commits, without a pooled connection.
A plan contains immutable generation paths, so a delayed worker cannot delete a
new upload of the same code. `pruned_at` makes bytes unavailable for use;
`purged_at` separately records successful cleanup. A failed or interrupted delete
remains a sweep candidate regardless of age or retained revision count. Repeating
a delete is safe, and completion is recorded only after all its storage operations
succeed. Both inline cleanup and scheduled cleanup honor the enable switch.

Retention removes source as well as executable builds. Lightweight tombstones and
run digests preserve provenance, but expired source cannot be inspected, rerun, or
promoted. This bounded policy is specified in the
[function](../product/journeys/automating-work.md#ps-func-004--a-person-can-change-a-function-without-breaking-what-is-running)
and [app](../product/journeys/packaging-and-reuse.md#ps-pack-030--a-person-builds-an-app-for-a-pod) contracts.

## Product surfaces

A preview host names an exact release and inherits the app's visibility. It does
not change the canonical live host. Client-supplied routing headers cannot select
an old release on that host. Promotion requires update permission. Pinning a
function run also requires update permission, and validates inputs against the
selected revision's schemas before creating a run.

The version panels distinguish failed requests from empty history and provide
retry actions. Selecting a historical function revision opens its input composer,
including when the user was inspecting a previous run.

Exports include source and dist when available. Vite imports rebuild source for
the destination pod: absence of a pod UUID in compiled output cannot establish
portability. Static source and dist-only apps retain their direct-import paths.

## Verification

Run `make quality`, `make quality-frontend`, the apps/function module suites,
function runtime tests, and the version-history DOM tests. Database-backed
regressions exercise delayed deletion after redeployment, pending cleanup below
the retention floor, rollback source attribution, and retained code identity.
