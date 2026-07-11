# Lemma backend module issue register

This register began as the static module-by-module review baseline. It now also
tracks the implementation state of the backend reliability/refactor PR. The
original finding text is retained below for audit context; the resolution
matrix is authoritative for current status.

## Priority model

| Priority | Meaning | Expected response |
| --- | --- | --- |
| P0 | Confirmed active compromise, unrecoverable corruption, or total outage | Stop release/operation and remediate immediately |
| P1 | High probability of data loss, duplicate side effects, credential exposure, authorization bypass, or resource exhaustion | Fix before the next production release |
| P2 | Material reliability, test, design, observability, or maintainability risk | Schedule in the next engineering cycle |
| P3 | Cleanup, consistency, documentation, or developer-experience improvement | Triage into normal backlog |

No P0 issue was confirmed in this review. The first pass should concentrate on
the P1 event, upload, token, cancellation, quota, and scheduling findings.

## Executive summary

| ID | Priority | Modules | Finding |
| --- | --- | --- | --- |
| EVT-001 | P1 | pod, agent_surfaces, workflow, datastore | Several Redis Stream consumers log and return on failure, which can acknowledge and lose required work |
| UPLOAD-001 | P1 | apps, datastore, icon, pod_bundle | Multipart bodies are fully buffered before size enforcement; several paths have no upload cap |
| BUNDLE-001 | P1 | pod_bundle | Cancel can report success while an apply continues mutating the pod |
| SECRET-001 | P1 | identity, connectors | Session/provider exceptions and token-bearing objects can reach logs or client error details |
| WORKSPACE-001 | P1 | workspace, identity | Delegated bearer tokens are cached as plaintext JSON in Redis |
| USAGE-001 | P1 | usage, agent | Limit check and reservation are not one atomic operation under concurrency |
| SCHEDULE-001 | P1 | schedule, agent, workflow | Agent-fire dedup fails open and filter failures silently drop schedule events |
| CONNECTOR-001 | P2 | connectors | An account credential endpoint returns raw access tokens to clients |
| CONNECTOR-002 | P2 | connectors | Dynamic schema compilation executes Python code in the API process |
| TEST-001 | P2 | all | CI reports coverage but enforces no threshold; schedule is at 54.2% unit coverage |
| TEST-002 | P2 | datastore and external integrations | Important optional/real-provider paths are skipped or opt-in rather than a routine gate |
| QUALITY-001 | P2 | all | Full production-module Ruff has 31 findings and no Python type-check gate exists |
| ARCH-001 | P2 | agent/surfaces, schedule/workflow, identity/pod, agent/workspace | Bidirectional imports make module boundaries non-independent |
| ARCH-002 | P2 | agent, agent_surfaces, connectors, datastore, pod_bundle, function | Multiple orchestration services are 700-2,200 lines and mix unrelated concerns |
| ERROR-001 | P2 | all, especially agent_surfaces | Exception policy is inconsistent and broad catches make retry/terminal behavior difficult to prove |
| OBS-001 | P2 | event-driven modules | No consistent dead-letter/replay contract or lag/failure SLO is visible at module boundaries |
| OSS-001 | P2 | backend/repository | Public-repo security and backend contribution guidance are missing |
| DOC-001 | P3 | agent_surfaces, workspace | Existing in-module READMEs are incomplete or stale relative to registered routes |
| API-001 | P3 | workflow, agent | Legacy `flow`/`assistant` vocabulary remains mixed with public `workflow`/`agent` naming |
| CLEAN-001 | P3 | usage, connectors, identity | Empty registrations, unused abstractions, and debug entrypoints add misleading surface area |

## PR resolution matrix

Status meanings: **Resolved** has an implemented control and direct automated
evidence; **Partial** has a shipped guardrail or meaningful reduction but the
original architectural/operational debt is not fully eliminated.

| ID | Status | Implementation and migration/API impact | Exact evidence |
| --- | --- | --- | --- |
| EVT-001 | **Resolved** | Versioned domain envelope, transactional PostgreSQL outbox/inbox, bounded dispatcher retry/DLQ, deterministic jobs, dual new-message/reclaim Redis subscribers, and operator replay commands. Redis Streams writes now have one core bus/outbox path; consumer context automatically propagates correlation and causation. All durable tables ship in the single `0003_backend_reliability` revision. | `app/core/tests/unit/test_reliability_foundations.py` (bulk staging + lineage); `app/core/infrastructure/events/tests/unit/test_stream_subscriber.py`; schedule E2E: 16 passed; datastore/apps E2E union: 90 passed, one protected GCS skip. |
| UPLOAD-001 | **Resolved** | Shared 1 MiB chunked staging with an 8 MiB spool threshold, request/module ceilings, SHA-256, archive inspection, and cleanup on failure. Limit violations use `413 UPLOAD_TOO_LARGE`. Apps, datastore, icon, and bundle controllers now pass staged paths to storage. | `test_reliability_foundations.py` upload/body-limit cases; `test_archive_validation.py`; `test_icon_api.py`; datastore/apps E2E union; app rollback tests in `test_app_storage_phase_session_free.py`. |
| BUNDLE-001 | **Resolved** | PostgreSQL-authoritative import jobs/checkpoints, monotonic terminal state, cancellation tombstones, cooperative checks around mutation/commit boundaries, `202 CANCELLING`, and explicit `CANCELLED`/`PARTIALLY_CANCELLED`. Tables are in revision `0003`. | `test_cancellation_state_machine.py` (19); `test_state_store.py` (12); `test_import_use_cases.py`; bundle/usage E2E: 22 passed, six protected Docker/real-runtime skips; focused live-worker cancellation E2E passed. |
| SECRET-001 | **Resolved** | Removed token-minting/debug output and session-object logging; centralized allowlist error details and nested secret redaction; provider errors expose only stable type/status/code fields. No database migration. | `test_error_handling_contract.py`; `test_reliability_foundations.py::test_canary_secrets_are_redacted_recursively_and_in_text`; `test_lemma_operation_gateway.py`; `test_webhook_controller_reliability.py`. |
| WORKSPACE-001 | **Resolved** | Workspace environment cache uses the shared versioned cipher, five-minute/token-bounded TTL, and user/pod/conversation/session/purpose-bound keys with revocation cleanup. | `test_workspace_env_cache.py`; `test_workspace_tool_runtime.py`; crypto rotation tests. |
| USAGE-001 | **Resolved** | When a deployment injects a limit port, canonical multi-scope counter upsert/locking keeps admission/reservation/record/release atomic; PostgreSQL `NULLS NOT DISTINCT` prevents nullable-scope duplicates. OSS defaults to unlimited metering, and missing model pricing records a null cost instead of rejecting work. Existing counter duplicates are merged by revision `0003`; no new usage table is added. | `test_usage_concurrency_e2e.py` (2); `test_usage_limits_fake_unit.py`; `test_system_pricing_unit.py`; `test_usage_entities.py`. |
| SCHEDULE-001 | **Resolved** | Durable schedule-run ledger keyed by schedule/source event, stable source IDs, failed/retryable filter outcomes, outbox target publication, retry/list APIs, and durable consecutive-failure state. Redis fire-store code was removed. Tables/column are in revision `0003`. | Full schedule E2E including schedule-run deduplication/retry; `test_schedule_processor.py`; `test_schedule_idempotency_regression.py`; `test_reliability_adapters.py`. |
| CONNECTOR-001 | **Resolved** | Raw credential GET route and generated Python/TypeScript SDK methods/types removed. This is a semver-major API/SDK change. | OpenAPI contract tests; Python SDK 44 passed/one skip; TypeScript SDK 115 passed; generated-client drift/build checks. |
| CONNECTOR-002 | **Resolved** | Replaced `exec()` with a restricted AST parser; removed unused runtime compiler injection; only allowlisted Pydantic declarations, literals, annotations, and containers are accepted. | `test_schema_compiler.py` (27), including hostile snippets, modern unions, constraints, recursion, and unsupported executable constructs. |
| TEST-001 | **Resolved** | CI combines raw coverage from seven E2E shards before unit data, publishes one module-wise `Unit`/`E2E only`/`Combined` PR table, and enforces 80% E2E-only for agent, agent surfaces, datastore, and function plus 70% combined overall, 65% combined schedule, and 85% changed code. | [Run 29087138000](https://github.com/lemma-work/lemma-platform/actions/runs/29087138000): 414 hermetic E2E journeys (411 passed, three protected/optional skips); agent 80.05%, agent surfaces 80.26%, datastore 80.80%, function 81.52% E2E-only; 84.95% combined overall, 78.59% combined schedule, 93% changed code. Unit/component suite: 2,200 run (2,199 passed, one optional skip). |
| TEST-002 | **Resolved** | Protected/scheduled workflow covers document processors, Docker AgentBox, scheduler recovery, OAuth connector, representative surface, real model budget, and commit-linked freshness artifacts. | `.github/workflows/backend-protected-e2e.yml`; locally protected external cases remain credential/runtime-gated by design. |
| QUALITY-001 | **Resolved** | Handwritten app/migrations/scripts are Ruff-clean; BasedPyright strict critical slice is mandatory; generated code is excluded from style but checked for codegen drift. | `uv run ruff check app migrations scripts` passed; `make typecheck-critical` reports 0 errors; `make architecture` passed. |
| ARCH-001 | **Partial** | Explicit contracts/events rule and architecture checker prevent new forbidden imports/cycles. Root composition and several contracts were extracted, but the inherited baseline still contains 59 forbidden import relationships and one strongly connected component. | `make architecture` passes the no-growth ratchet and reports `59 inherited import violations, 1 inherited cycles`. Full zero-baseline cleanup remains follow-up work. |
| ARCH-002 | **Partial** | Datastore record/storage phases, apps storage/use-case phases, bundle planning/staging/applying/checkpointing, and connector operation routing were extracted. Complexity and >600-line files cannot grow, but several original orchestration files remain oversized. | Architecture complexity/oversized-file ratchet passed; extracted-component unit tests pass. Completing all seven service splits remains follow-up work. |
| ERROR-001 | **Partial** | Unified `DomainError` taxonomy/envelope, cancellation preservation, sanitized provider mapping, Ruff `BLE001`, and a broad-catch no-growth gate are in place. Historical process-boundary catches remain and are not all converted. | `test_error_handling_contract.py`; canary/redaction tests; architecture broad-catch ratchet passed. |
| OBS-001 | **Partial** | Event/correlation/request identifiers with automatic parent/chain propagation, outbox/inbox attempts/outcomes, retry/DLQ metrics, worker watchdog, schedule-run state, bundle checkpoints, and operator replay/SLO guidance are implemented. A production dashboard/alert deployment is environment-owned and not part of this repository diff. | `docs/operators/reliability.md`; reliability lineage/outbox tests; schedule-run API/E2E; outbox dispatcher tests; `test_realtime_channels.py`. |
| OSS-001 | **Resolved** | Added coordinated disclosure, contribution guide, code of conduct, threat model, generated-code policy, release checklist, Dependabot, CodeQL, dependency review, Gitleaks, `pip-audit`, and Trivy. | Root governance files and `.github/workflows/security.yml`; workflow syntax is reviewed in CI. |
| DOC-001 | **Resolved** | `docs/modules/*.md` is canonical; stale module READMEs point there; OpenAPI route inventory is generated and checked. | `make architecture` includes `generate_route_inventory.py --check` and passed. |
| API-001 | **Resolved** | Public `assistant`/`flow` schemas and OpenAPI aliases removed; Python vocabulary uses agent/workflow while legacy database table names remain unchanged. Both SDKs and frontend call sites were regenerated/updated. | Backend schema tests; Python/TypeScript SDK tests; frontend production build (69 pages) passed. |
| CLEAN-001 | **Resolved** | Removed empty usage event router, unused compiler injection, token-print entrypoint, obsolete flow controllers/models/services/templates, and generated credential/legacy alias types. | Ruff passed; registry/OpenAPI/route inventory tests passed; full unit suite passed. |

### Remaining non-blocking architecture/operations debt

`ARCH-001`, `ARCH-002`, `ERROR-001`, and `OBS-001` remain explicitly Partial.
The PR converts them from unbounded/unmeasured risk into enforced ratchets and
durable operational controls, but it does not pretend that a 300-file breaking
reliability change also eliminated every inherited cycle, oversized service,
historical broad catch, or deployed dashboard dependency.

## P1 findings

### EVT-001 — Redis Stream handlers can lose events after transient failures

**Evidence**

- Pod schema provisioning previously caught every exception and returned after
  logging. It now uses the durable outbox/grouped inbox consumer and rethrows
  infrastructure failures for bounded retry/DLQ handling. First-table creation
  independently bootstraps the same schema under an advisory lock.
- Surface webhook and scheduled-ingress consumers wrap the entire handler,
  including streaq enqueue, and return on failure:
  [`agent_surfaces/events/handlers.py`](../../app/modules/agent_surfaces/events/handlers.py#L70).
- Workflow consumers catch job-enqueue failures and do not re-raise:
  [`workflow/events/handlers.py`](../../app/modules/workflow/events/handlers.py#L70),
  [`workflow/events/handlers.py`](../../app/modules/workflow/events/handlers.py#L263).
- Datastore's file-event consumer also logs and returns, although its stale-file
  recovery cron eventually backstops many of these cases:
  [`datastore/events/handlers.py`](../../app/modules/datastore/events/handlers.py#L102).

**Impact**

Once the stream message is acknowledged, a transient Redis/DB/queue error can
become a permanently missing schema, lost inbound message, or workflow that
never starts/resumes. Recovery behavior varies by module and is not obvious to
operators.

**Recommended fix**

1. Define one subscriber policy: validation/terminal errors are explicitly
   acknowledged; infrastructure errors re-raise for retry.
2. Add bounded retry plus a dead-letter stream containing event id, type,
   request/correlation id, exception class, and attempt count.
3. Keep infrastructure delivery state in the event outbox/inbox rather than on
   the pod aggregate; make schema creation and first-table bootstrap idempotent.
4. Add fault-injection tests for DB/Redis/streaq failure between receive,
   enqueue, and acknowledgement.

**Acceptance criteria**

- Killing Redis/streaq/DB during each consumer does not silently lose work.
- A failed schema-creation delivery is visible/replayable through the event
  inbox/DLQ, and the first table write repairs a missing physical schema.
- DLQ/replay behavior is documented and alertable.

### UPLOAD-001 — unbounded multipart buffering can exhaust API memory/storage

**Evidence**

The following controllers call `await UploadFile.read()` with no chunk size:

- [`datastore/file_controller.py`](../../app/modules/datastore/api/controllers/file_controller.py#L143)
  for original files, replacement content, Markdown, and every attached image.
- [`apps/app_controller.py`](../../app/modules/apps/api/controllers/app_controller.py#L242)
  for both source and dist archives.
- [`icon/icon_controller.py`](../../app/modules/icon/api/controllers/icon_controller.py#L34).
- [`pod_bundle/import_controller.py`](../../app/modules/pod_bundle/api/controllers/import_controller.py#L98);
  its 100 MB check occurs only after the full body is in memory in
  [`import_use_cases.py`](../../app/modules/pod_bundle/application/import_use_cases.py#L106).

Datastore's `document_processing_max_file_bytes` is checked later by the worker;
it does not protect the upload request. Icon and app upload paths have no clear
module byte cap.

**Impact**

Any authenticated user with the relevant write permission can create high
resident memory, request-worker stalls, temporary-disk pressure, and persistent
storage cost. Concurrent uploads multiply the effect.

**Recommended fix**

- Add a global request-body ceiling at the ingress/proxy and an ASGI streaming
  guard that does not rely only on a client-supplied `Content-Length`.
- Stream uploads in bounded chunks directly to staging/object storage while
  counting bytes and hashing.
- Define separate configurable limits for icons, datastore originals,
  Markdown/image batches, app source/dist archives, and pod bundles.
- Abort and delete partial objects on limit, disconnect, timeout, or validation
  failure; test chunked transfer without `Content-Length`.

**Acceptance criteria**

An over-limit request returns `413` before buffering the body, process memory
stays bounded under concurrent uploads, and no partial bytes remain.

### BUNDLE-001 — pod-bundle cancellation is not a durable stop guarantee

**Evidence**

`cancel_import()` waits only two seconds for best-effort abort, swallows abort
and staging-delete errors, deletes Redis state, and returns:
[`pod_bundle/application/import_use_cases.py`](../../app/modules/pod_bundle/application/import_use_cases.py#L321).
The apply worker loads state once and then loops through all pending steps
without checking a cancellation token/state between mutations:
[`pod_bundle/events/handlers.py`](../../app/modules/pod_bundle/events/handlers.py#L354),
[`pod_bundle/events/handlers.py`](../../app/modules/pod_bundle/events/handlers.py#L433).
If it already extracted the archive, deleting staging does not stop it; later
checkpoints can recreate the Redis document.

**Impact**

The API can say an import was cancelled while tables, functions, agents,
workflows, apps, or grants continue changing. This violates user expectation
and makes incident recovery ambiguous.

**Recommended fix**

- Persist a cancellation tombstone with a retention TTL instead of deleting
  state immediately.
- Check it before each step, after every external call, and before commit; make
  long AgentBox builds cooperatively cancellable.
- Return `202 CANCELLING` until the worker confirms `CANCELLED`; only then clean
  staging/state.
- Define whether already committed steps remain (partial cancellation) or must
  be compensated, and expose that outcome in status.

**Acceptance criteria**

A race test that cancels during every step type proves no later step commits and
the terminal state cannot revert from `CANCELLED`.

### SECRET-001 — sensitive values can escape through logs and error details

**Evidence**

- `get_user_token()` logs the complete SuperTokens session object, and the file
  contains a runnable debug entrypoint that prints a minted token:
  [`identity/.../helpers.py`](../../app/modules/identity/infrastructure/supertokens_auth/helpers.py#L22),
  [`identity/.../helpers.py`](../../app/modules/identity/infrastructure/supertokens_auth/helpers.py#L91).
- CLI refresh includes `str(exc)` in the client-visible 401 details:
  [`identity/auth_controller.py`](../../app/modules/identity/api/controllers/auth_controller.py#L161).
- Connector OAuth failures log the provider exception and return its string and
  extracted details to the client:
  [`connectors/connector_service.py`](../../app/modules/connectors/services/connector_service.py#L943).

Provider/library exception strings can contain request bodies, callback data,
connection ids, or tokens. Even if the current SuperTokens `repr` is benign,
logging the session object makes safety depend on a third-party representation.

**Recommended fix**

- Delete the debug `__main__` token mint and never log session/credential
  objects.
- Centralize exception classification/redaction; clients receive a stable code
  and request id, while structured logs contain allowlisted provider/status
  fields only.
- Add log-capture tests with canary tokens and a repository secret-scanning
  check.

**Acceptance criteria**

Canary access/refresh/client secrets never appear in API bodies, application
logs, tracing attributes, or captured exceptions for success and failure paths.

### WORKSPACE-001 — delegated tokens are cached unencrypted in Redis

**Evidence**

`WorkspaceSandboxService` mints a bearer token and includes it in
`LEMMA_TOKEN`: [`workspace_sandbox_service.py`](../../app/modules/workspace/services/workspace_sandbox_service.py#L411).
`WorkspaceToolRuntime` then stores the entire `session.env_vars` dictionary:
[`workspace_tool_runtime.py`](../../app/modules/workspace/services/workspace_tool_runtime.py#L174).
`RedisWorkspaceEnvCache` serializes that dictionary directly as JSON for up to
30 minutes: [`workspace_env_cache.py`](../../app/modules/workspace/services/workspace_env_cache.py#L26).

This conflicts with the backend's documented rule that API keys/tokens remain
secret values and are encrypted at rest. A Redis snapshot, debug console, broad
cache access, or accidental dump yields usable delegated tokens until expiry.

**Recommended fix**

Prefer caching a token handle/derivation input and minting short-lived tokens on
session acquisition. If reuse is required, encrypt the value with the shared
versioned secret cipher, isolate the Redis keyspace/ACL, shorten TTLs, and bind
the cache key to the actual authorization session. Ensure revocation clears all
matching values.

**Acceptance criteria**

No bearer token is readable in Redis plaintext, rotation/revocation tests pass,
and a token for one conversation/session cannot be reused as another.

### USAGE-001 — usage admission and reservation are not atomic

**Evidence**

`reserve_for_profile()` first calls `get_usage_limits()` and then performs one
or more counter reservations:
[`usage_service.py`](../../app/modules/usage/services/usage_service.py#L75).
`get_usage_limits()` reads used and reserved totals in separate queries:
[`usage_service.py`](../../app/modules/usage/services/usage_service.py#L372).
`reserve_counter()` locks only an existing counter row and creates one after a
no-row read: [`usage/repositories.py`](../../app/modules/usage/infrastructure/repositories.py#L217).

Two concurrent requests can both observe `allowed=true`, then both reserve and
cross the configured limit. On a new time window, concurrent no-row inserts can
also race the unique constraint instead of producing a clean admission result.

**Recommended fix**

Use one transaction and one atomic upsert/conditional update per limit scope,
locking a deterministic counter row before evaluating `used + reserved + new`.
Reserve all scopes consistently or roll all of them back. Add concurrency tests
at the exact limit and at new week/month boundaries.

**Acceptance criteria**

Under high concurrency, admitted reservations never exceed the configured cap,
and rejected requests return the domain limit error rather than an integrity
error.

### SCHEDULE-001 — schedule failure policy can duplicate or silently drop work

**Evidence**

- Agent-target fire dedup explicitly returns `True` on any Redis error and when
  no stable event id exists:
  [`schedule_fire_store.py`](../../app/modules/schedule/services/schedule_fire_store.py#L60).
  The file notes that agent targets lack a database uniqueness constraint.
- `ScheduleProcessor` catches every LLM filter error and returns `False`, so the
  fire is neither retried nor represented as failed:
  [`schedule_processor.py`](../../app/modules/schedule/services/schedule_processor.py#L30).
- `WebhookHandler` catches per-schedule publication/enqueue failures and lets
  the overall webhook succeed:
  [`webhook_handler.py`](../../app/modules/schedule/services/webhook_handler.py#L76).

**Impact**

A Redis outage/redelivery can start duplicate agent conversations and external
side effects; a provider/model/queue outage can silently discard a legitimate
trigger. Neither result is visible as a durable schedule-fire record.

**Recommended fix**

Persist a schedule-run ledger keyed by `(schedule_id, source_event_id)` with
status/attempt/error and a unique constraint for every target type. Make
delivery an outbox-driven state machine. Reject or quarantine events without a
stable provider id when exactly-once effects are required. Record filter errors
and retry according to classification rather than treating them as `filtered
out`.

**Acceptance criteria**

Redis loss and message redelivery cannot create two target runs for one fire;
every accepted source event is queryable as delivered, filtered, failed, or
dead-lettered.

## P2 findings

### CONNECTOR-001 — raw access tokens are returned by a general GET endpoint

[`connectors/account_controller.py`](../../app/modules/connectors/api/account_controller.py#L113)
returns `credentials.access_token` for an account. Ownership checks are present,
but a normal resource GET now expands the places where provider tokens can
appear: browser memory, SDK debug output, tracing, proxy logs, and extensions.

Replace this with server-side operation execution or a narrowly scoped,
short-lived exchange token. If raw retrieval is unavoidable, require explicit
step-up/approval, `POST`, no-store headers, audit logging, and a schema that
never returns refresh/client secrets. Add a test that verifies logs/traces do
not capture the response body.

### CONNECTOR-002 — schema compilation executes Python in the API process

[`connectors/schema_compiler.py`](../../app/modules/connectors/infrastructure/adapters/schema_compiler.py#L15)
calls `exec(code, namespace)`. The compiler is injected into the production
operation service even though that service currently does not use it:
[`connectors/api/dependencies.py`](../../app/modules/connectors/api/dependencies.py#L137).
Catalog import uses it for generated connector snippets, so a compromised or
unexpected catalog source becomes arbitrary code execution with API/importer
process privileges.

Parse a restricted AST/JSON schema format or run compilation inside AgentBox
with no secrets/network and strict time/memory limits. Remove the unused
runtime dependency. Add hostile snippets (`import os`, file/network access,
infinite loop) to security tests.

### TEST-001 — coverage is report-only and below a risk-adjusted floor

Current `make coverage-backend-unit` result (2026-07-09): 2,007 tests, 2,006
passed, one skipped, 63.9% total. Per-module coverage ranges from schedule 54.2%
to icon 84.0%. The generated report says it is report-only; the CI command does
not pass `--fail-under`, while the manual `coverage-module` target requires 90%.

Adopt a ratchet: fail a PR if total or a touched module drops, then raise floors
incrementally. Start critical floors for authentication/delegation, event
consumers, scheduler fires, bundle cancellation, file upload/security, and
usage concurrency. Track branch coverage as well as lines.

### TEST-002 — optional and real integration paths are not routine gates

The unit run skips MarkItDown when the optional package is absent. Backend e2e
is an opt-in label/manual workflow, while real model, provider, AgentBox,
scheduler, and live-surface tests require credentials/runtime flags. This is
reasonable for cost, but it leaves release-critical adapters dependent on
manual execution.

Create scheduled/nightly protected-environment matrices for document
processors, Postgres/Redis outage recovery, Docker AgentBox, scheduler, one
native OAuth connector, and one representative surface. Publish freshness and
results in release checks; require a recent green run before release.

### QUALITY-001 — the advertised full lint target is not green or enforced

`make lint-async` passes and is the CI backend lint. `uv run ruff check
app/modules --exclude '*/tests/*' --statistics` currently reports 31 production
findings: 19 `E402`, six `F401`, four `F821`, one `E731`, and one `F541`.
`make lint` reports 13,938 repository findings, mostly generated connector code,
which makes the documented command unusable as a contributor gate. There is no
backend mypy/Pyright command in the project or CI.

Define separate Ruff source sets for handwritten production, tests, migrations,
and generated code; make the handwritten set green and mandatory. Add a gradual
type-check configuration with a baseline/strictness ratchet, starting at domain
ports, DTOs, authorization, and event payloads.

### ARCH-001 — module boundaries contain multiple import cycles

Examples include:

- agent surfaces imports agent API/services/tools, while agent capability
  assembly imports surface platform capabilities;
- workflow imports schedule value objects/adapters, while schedule imports
  workflow services;
- pod imports identity domain/infrastructure models, while identity's pod
  membership adapter/auth controller imports pod internals;
- agent imports workspace services and workspace imports identity/pod internals;
- datastore imports agent file helpers and agent imports datastore services.

This contradicts the registry contract's stated ports/adapters boundary and
makes it hard to mount/test/remove modules independently. Define stable public
ports/events in consumer-owned domain packages, compose adapters at the root,
and add an import-linter rule that forbids another module's `api`, `services`,
or `infrastructure` packages. Allow only explicit public contracts.

### ARCH-002 — oversized services mix orchestration, policy, and adapters

Representative production files are
`agent_surfaces/services/ingress_service.py` (2,218 lines),
`agent/services/conversation_service.py` (1,563),
`connectors/services/connector_service.py` (1,405),
`agent/infrastructure/harnesses/pydantic_ai.py` (1,309),
`agent/services/agent_runner_service.py` (1,072),
`pod_bundle/infrastructure/exporter.py` (990), and
`function/application/function_run_executor.py` (970).

Size alone is not a defect, but these classes contain policy, persistence
coordination, provider branching, error classification, serialization, and
recovery. Split by use case/state machine and make phases explicit DTO-to-DTO
functions. Establish a review threshold (for example, flag files over 500 lines
or functions over a complexity budget) without blindly enforcing line count.

### ERROR-001 — broad exception handling lacks a uniform taxonomy

Production modules contain 374 `except Exception`/`except BaseException`
sites. Many are intentional boundaries, but behavior varies between swallow,
fallback, state transition, retry, rethrow, and exposing `str(exc)`. The highest
concentration is agent surfaces (114 sites), followed by agent (68), datastore
(60), pod bundle (40), schedule (29), and connectors (28).

Create and document an error matrix by boundary: domain validation, conflict,
authorization, not-found, provider retryable, provider terminal, infrastructure
retryable, cancellation, and programming error. Require each catch to classify,
set durable state where relevant, preserve cancellation, and either rethrow or
explicitly justify a safe fallback. Add a lint/review check for newly introduced
broad catches.

### OBS-001 — event-driven modules lack a shared operational contract

The code has request ids and structured logs, but module event paths do not
consistently expose consumer lag, retry count, DLQ size, oldest event, job queue
latency, or durable correlation from source event to resulting run. Some modules
have reconcilers; others depend on logs.

Standardize event metadata (`event_id`, causation/correlation/request ids,
occurred_at, schema version), metrics, trace propagation, retry/DLQ naming, and
operator replay tooling. Add dashboards/alerts for stream lag, failed jobs,
orphaned waits/runs, stuck file processing, pod schema-bootstrap DLQ outcomes,
and schedule-run outcomes.

### OSS-001 — public backend contribution and security processes are missing

The repository has a frontend-only `CONTRIBUTING.md`, but no root/backend
contribution guide, `SECURITY.md`, or `CODE_OF_CONDUCT.md`. CI also has no
visible dependency-vulnerability, secret, SAST/CodeQL, or container scan for
the backend.

Add coordinated disclosure instructions, supported versions, response targets,
threat-model/security-boundary notes, local test/lint/migration commands, module
ownership/review expectations, generated-code policy, and release checklist.
Enable dependency review/Dependabot, secret scanning, CodeQL or equivalent,
Python dependency audit, and image scanning with a documented triage policy.

## P3 findings

### DOC-001 — legacy module READMEs have drifted

`app/modules/agent_surfaces/README.md` documents routes such as a surface-id
CRUD path, `/toggle`, and platform checklist that do not match the registered
name-based routes and current setup/catalog endpoints. The workspace README
covers only the AgentBox model/config and omits public endpoints, Redis caches,
delegated token behavior, browser access, retry boundaries, and consumers.

Make `docs/modules/*.md` the canonical architecture docs, link them from the
module READMEs or replace the READMEs with short pointers, and add a test/script
that extracts OpenAPI operation ids/prefixes into a route inventory so docs
cannot silently drift.

### API-001 — legacy vocabulary obscures the public model

The workflow package and database classes still use `Flow*` while the public API
uses workflow names. Agent APIs/docs also retain `assistant` compatibility in
places. Keep wire compatibility, but select one domain vocabulary for new code,
mark legacy schemas/hooks explicitly, and plan versioned removal rather than
adding more aliases.

### CLEAN-001 — misleading dead/debug surface should be removed

- The usage module registers an empty Redis router:
  [`usage/events/handlers.py`](../../app/modules/usage/events/handlers.py#L1).
- `ConnectorOperationService` accepts/stores a schema compiler it does not call.
- Identity's SuperTokens helper has the token-printing `__main__` block covered
  by SECRET-001.

Remove unused registrations/dependencies, or implement and document their
intended behavior. Dead extension points make architecture harder to reason
about and hide whether an event is deliberately unconsumed.

## Suggested execution order

1. **Reliability/security stop-the-bleeding:** EVT-001, UPLOAD-001, BUNDLE-001,
   SECRET-001, WORKSPACE-001, USAGE-001, SCHEDULE-001.
2. **Put guardrails in CI:** TEST-001, QUALITY-001, security scanning from
   OSS-001, plus failure-injection tests for the P1 fixes.
3. **Make operations diagnosable:** OBS-001 and nightly integration coverage
   from TEST-002.
4. **Reduce change risk:** ARCH-001, ARCH-002, ERROR-001, then the P3 cleanup.

## Verification performed

| Command/check | Result |
| --- | --- |
| `make test-unit` / unit coverage suite | 2,200 run: 2,199 passed, one optional test skipped, 534 E2E/protected tests deselected |
| Authoritative E2E-only union | 414 run across seven shards: 411 passed, three protected/optional skips; agent 80.05%, agent surfaces 80.26%, datastore 80.80%, function 81.52%; all 80% critical-module floors passed |
| Aggregate unit + E2E coverage | 84.95% total; schedule 78.59%; changed code 93%; all configured floors passed |
| GitHub mocked container E2E | [Run 29087138000](https://github.com/lemma-work/lemma-platform/actions/runs/29087138000): all seven shards passed in parallel — agent, surfaces, datastore/apps, function, connectors/schedule, pod, and catch-all rest |
| `uv run ruff check app migrations scripts` | Passed |
| `make typecheck-critical` | 0 errors, 0 warnings, 0 notes |
| `make architecture` | Passed; route inventory current; no baseline growth (59 inherited forbidden-import relationships and one inherited cycle remain) |
| `uv run python scripts/verify_reliability_migration.py` | Clean install, downgrade, legacy nullable-counter deduplication, and re-upgrade passed against PostgreSQL |
| `pytest app/modules/schedule/tests/e2e` | Cron, webhook, datastore, schedule-run ledger, retry, authorization, and telemetry |
| Pod-bundle + usage E2E | 22 passed; six Docker/real-runtime protected cases skipped locally |
| Datastore + apps E2E | Dedicated datastore/apps shard passed, including typed-record, upload cleanup, file processing/recovery, WebSocket, and search journeys; its raw coverage contributes to the authoritative union |
| Pod schema bootstrap | Durable consumer retry unit tests and datastore DDL concurrency tests cover rethrow/replay and the shared PostgreSQL advisory lock; infrastructure state is not stored on `pods`. |
| Python / TypeScript SDK | Python 44 passed, one skip; TypeScript 115 passed; TypeScript production/browser bundles built |
| CLI / frontend | CLI 585 passed (24 deselected); frontend production build passed with 69 pages |

Protected provider/AgentBox/live-surface cases remain intentionally gated on
credentials and a stable Docker runner. The repository workflow publishes
commit-linked results and applies release freshness checks; local Docker
Desktop instability was recorded as infrastructure failure rather than
misreported as product failures.
