# Usage accounting and ongoing limits

Usage owns spending authority and immutable receipts. `agent_runs` remains an
execution ledger; it has no balance, cost, or settlement columns. A run's direct
usage is selected by `agent_run_id`. A conversation includes each attributed
receipt once, including child runs; parent totals are never added to child totals.

## Admission and checkpoints

For every applicable organization or user window, admission locks the counter
and enforces `confirmed spend + outstanding allocations <= limit`. All applicable
counters commit together. An allocation belongs to one execution, payer, model,
and frozen rate card. It cannot be transferred to another execution.

Before each provider request, the local meter checks its remaining allocation
against a conservative request bound. The bound uses the model's context ceiling,
maximum token rates across pricing tiers and cache premiums, and an enforced
output ceiling. Provider SDK and HTTP retries are disabled beneath this boundary:
a retry must obtain its own request authority. Tool invocations do not count as
model requests, but an LLM called from a tool does.

Actual usage is priced per request, then summed. Pricing aggregated batch tokens
would incorrectly cross per-request tiers. Cache tokens are retained separately.
The pinned `genai-prices` dataset supplies provider/model estimates without a
network lookup; explicit registered prices take precedence. A name-only match
behind an unknown gateway is a reporting estimate, not sufficient for admission.
Unknown models remain usable when no monetary limit applies. With a monetary
limit, missing prices, missing context bounds, and provider-native tools whose
charges cannot be bounded are refused before dispatch. The current adapter also
refuses native compaction/advisor, one-hour cache modes, premium service tiers,
and raw provider body overrides under monetary caps. Their charges or effective
request ceilings cannot be verified against the frozen rate card. Admission
checks the merged model defaults and request settings used by the provider.

The local meter checkpoints after `USAGE_BATCH_REQUESTS` (default 10) or
`USAGE_BATCH_SECONDS` (30), and closes at execution exit. A request that cannot
fit first closes the old allocation and obtains new authority. Window rollover
also requires a new allocation. `USAGE_BUDGET_CHUNK_USD` (1) is a target, not a
minimum: near a limit the remaining amount may be granted, and a single request
may need more. Bounds above the target also request one target of headroom
so a large context ceiling need not force a database write after every call.
A large context bound can still force earlier checkpoints; batching is
an optimization, not permission to overspend. `USAGE_REQUEST_OUTPUT_CEILING`
(8192) is the default per-request ceiling for limited executions; an explicit
model setting supplies the ceiling when present.

No database session stays open across provider I/O. Checkpoints update counters,
write a receipt, and collect the model usage event in one unit of work. The outbox
publishes after commit. An allowance warning is recorded once per counter window
when confirmed spend crosses `USAGE_LIMIT_WARN_FRACTION` (0.8). A lowered policy
applies to new allocations; existing allocations remain outstanding authority. An unlimited execution
rechecks policy at each checkpoint and must obtain a bounded allocation before
its next request when a monetary limit has been introduced.

## Failure and recovery

A checkpoint is identified by allocation and sequence, with a digest of the
immutable payload. Losing the commit acknowledgement is safe: the same payload
can be retried without charging again, including allocation closure and renewal.
A conflicting payload is rejected.
Empty heartbeats and closes update allocation state without inventing model usage.

A failed or interrupted request without a final provider receipt retains its
entire request bound as uncertain. Unused authority is released on a graceful
close; uncertainty is not. Expired allocations become uncertain during bounded
recovery on subsequent admissions. `USAGE_ALLOCATION_TIMEOUT_SECONDS` (120)
classifies an abandoned allocation; it never authorizes a refund. A late receipt
from an expired worker can settle its original allocation idempotently.
Outstanding authority ceases to affect admission when its budget window ends.

An early normal stream exit also lacks a final receipt. Only a complete response
settles measured cost; incomplete, interrupted, or suspended responses retain
authority. All-zero token usage also retains authority: provider adapters use it
when the response omits usage, so it cannot prove a free request.
If a final reported cost exceeds the authorized bound, the run fails,
but the full known charge still counts toward subsequent admission. The receipt
preserves token counts and `over_bound_cost_usd` for investigation. Settlement
releases at most the available hold; any concurrent request that becomes uncertain
still retains its liability, without granting new spending authority.

There is deliberately no automatic reconciliation against a provider invoice.
An explicitly uncertain request cannot be relabeled as an actual charge without
external evidence. Operators must retain the allocation and receipt history when
investigating it. The usage API exposes confirmed cost and cached-token fields;
receipt metadata contains the rate snapshot and uncertain amount. Existing
`reserved_usd` includes both live and uncertain authority.

Remote agent hosts using their own provider credentials remain observable through
the existing run receipts. A runtime outside Lemma's dispatch control is refused
for a limited system-paid profile: a run-start check cannot enforce ongoing spend.
Embeddings, speech and search metering are outside this LLM allocation path.

## Monetary precision

Authoritative receipt costs, allocations and budget counters use PostgreSQL
`NUMERIC(24, 9)`: fifteen integer digits and nine fractional USD digits. Pricing
uses `Decimal`, rounds each request's total upward to the nearest nanodollar,
and adds those rounded charges exactly. Rounding adds less than one nanodollar
per priced request; it never silently discards a tiny positive charge.

Reports and initial budget counters sum the exact cost column. Historical rows
without that column are converted from their legacy float to nine decimal places
before aggregation; this cannot recover precision already lost in old records.
The old `cost_usd` field remains a compatibility number for API responses and
telemetry, not the authoritative cost for new receipts or budget accounting.

## Migration and verification

Migration `0030_usage_allocations` adds the allocation table, nullable batch and
cache fields on existing receipts, and fixed-precision counters. Historical
rows keep their original cost and receive `LEGACY` provenance; there is no token
or cost backfill. The first counter for a window seeds from existing history.

Drain old workers before migrating and deploying the new writer. Old workers use
aggregate run settlement and must not overlap the allocation writer. Downgrade
requires draining new workers and retaining an accounting export: it removes new
allocation and provenance fields and converts counters back to floating point.

From `lemma-backend/`, run `uv run pytest app/modules/usage/tests/unit` for
arithmetic and batching, and `uv run pytest app/modules/usage/tests/e2e` for real
PostgreSQL concurrency, late receipts, wrapper failures, events and migration
round trips. The batch-boundary unit test measures database gateway calls rather
than asserting an environment-dependent latency. Run `make quality` at the repo
root and the backend unit lane before merging.

For a configured provider, run `E2E_LLM_MODE=real uv run pytest
app/modules/usage/tests/e2e/test_real_model_accounting_e2e.py -s` from the backend.
This opt-in check makes small real agent requests against disposable PostgreSQL,
compares provider tokens with receipts and independent rate arithmetic, and verifies
ongoing admission. It preserves deployment pricing and excludes the deterministic
suite's synthetic rates. The result checks the recorded estimate, not an invoice.
`make typecheck-critical` checks the accounting tests with their own configuration
so the backend's general test exclusions cannot silently skip them.
