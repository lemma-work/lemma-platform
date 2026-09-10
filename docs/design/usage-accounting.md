# Usage accounting and ongoing limits

Usage owns provider-request receipts and shared spending counters. `agent_runs`
remains an execution ledger without accounting columns. Direct run usage is
selected by `agent_run_id`; conversation usage includes each attributed receipt
once, including child runs. Parent totals are never added to child totals.

## Check, dispatch, record

Run start and every LLM provider attempt check current organization and user budgets
before dispatch. Request checks refresh shared totals from the ledger and current policy, so ongoing runs
observe usage from other workers and newly configured limits. A reached limit
stops the next request. There are no budget allocations, reservations, prompt
estimates, local grants or checkpoint batches.

Before provider I/O, admission commits a request journal entry marked `PENDING`.
Afterward, settlement records the actual reported tokens and their per-request
price, updates applicable counters, and rechecks the current budget. A request
that crosses the limit is charged in full; its successful output is retained,
and further dispatch is stopped. The request id makes settlement idempotent:
replaying a committed result cannot charge it twice. Conflicting settlement
payloads are rejected.

This policy deliberately permits overshoot. Requests already admitted by
concurrent workers can finish after another request crosses a limit. An
individual request can also exceed the remaining budget. There is no fixed
monetary overshoot bound independent of concurrency and request size. Checking
before every dispatch prevents a long-running agent from continuing after a
recorded breach, but cannot revoke requests already in flight.

`USAGE_REQUEST_OUTPUT_CEILING` supplies the default output cap of 8192 tokens.
The output cap limits request size; it does not reserve money or establish an
input-token estimate. No database transaction stays open across provider I/O.
Counter updates, receipt settlement and outbox events commit together.

Spend is attributed to the request's original start window. The post-response
limit check uses the current time and policy, including window rollover. Warning
thresholds use the current limit and `USAGE_LIMIT_WARN_FRACTION`; changing a limit
resets its warning latch.

## Pricing and supported requests

Pricing happens per provider request, before adding costs to shared counters.
Combining tokens across requests before pricing would incorrectly cross
per-request pricing tiers. Decimal arithmetic rounds each charge upward to the
nearest nanodollar. Explicit zero prices are valid; a missing price is unknown,
not zero. A known subtotal remains useful when other requests are unpriced;
uncertainty metadata records what that subtotal cannot explain.

The pinned `genai-prices` snapshot supplies catalog rates without network
lookups. Configured prices take precedence. Each receipt retains the applied rate
card and its version so its cost can be reproduced. A model-name match behind an
unknown gateway can support reporting, but limited usage requires a trusted provider
endpoint or configured prices. Pricing does not require a model context ceiling.
Provider invoices remain the authority for reconciliation. Updating the snapshot
is a reviewed release change; no background refresh changes request pricing.

Limited requests require input and output token prices and a supported billing
mode. Native provider tools, unsupported multimedia, image output, one-hour cache
writes, premium service tiers and raw provider body overrides are rejected where
their additional costs cannot be represented faithfully. The check covers model
defaults, request settings and replayed history. Ordinary JSON function tools
and signed reasoning remain supported. Missing prices for positive audio or
cache usage produce an unknown cost rather than a fabricated zero.

Unpriced models remain usable without monetary limits. Organization-owned
provider credentials (BYOK) do not acquire system-paid budget limits. Limited
SYSTEM profiles cannot run on remote agent hosts outside Lemma's dispatch
control. Remote hosts using their own provider credentials retain their existing
observed-usage path. Embeddings, speech and search are outside this LLM path.

## Failures and uncertainty

Each provider attempt has its own journal entry, including retries. SDK and HTTP
retries below the metered boundary are disabled so retry attempts cannot bypass
the budget check. The boundary uses bounded retries for supported transient
failures. In the agent harness, the graph driver owns connection recovery, including
drops before the first token, so it can reset partial output and restore
history. Every re-entered request still passes through metering. Standalone model
callers retain pre-stream retries; a stream already handed to its consumer is
not silently replayed inside the model. Explicit retryable HTTP responses remain
handled at the model boundary, including their `Retry-After` headers.

Confirmed provider rejections with no billable work can settle at zero cost.
Timeouts, lost responses, missing final usage and worker crashes cannot establish
what the provider charged. Their entries remain visibly pending or unconfirmed;
the system does not invent token counts, estimated charges or reserved holds.
A journal entry is evidence of an attempted request, not evidence of its cost.
If a returned response cannot be priced, a limited execution preserves that
response but stops subsequent calls on that meter. Transient provider failures
still have a bounded retry count, with a budget check before each attempt.

This design cannot automatically recover missing provider usage after a worker
crash or prove that unknown charges did not exceed a budget. Operator or provider
reconciliation requires independent evidence. Normal shutdown settles available
receipts before finalizing run metadata. Repeated finalization does not insert
another charge. Unknown or pending attempts must remain available for audit;
there is no age-based claim that they were free.

## Precision and migration

Authoritative costs and shared counters use PostgreSQL `NUMERIC(24, 9)`:
fifteen integer and nine fractional USD digits. Historical floating-point costs
are converted before aggregation; this cannot recover precision already lost.
The compatibility `cost_usd` field is not the authority for new receipts.

The usage migration follows the app/function history migrations. It adds exact
costs, pricing provenance and a partial unique request-id index for idempotent
settlement. It does not create allocation tables. Historical usage remains
available with legacy provenance. Checks refresh totals from history so policy
changes and compatibility writers cannot leave stale cached allowance. The request-id index is built concurrently so the migration does not
require a blocking index build on existing usage rows.

Drain old workers before migration and deployment: aggregate run settlement
must not overlap per-request settlement. Downgrade requires draining new workers
and retaining an accounting export before removing exact-cost and request-journal
fields. Receipts and their replay identifiers must share the same retention
horizon.

## Verification

From the backend, run `uv run pytest app/modules/usage/tests/unit` for request
pricing and dispatch behavior, and `uv run pytest app/modules/usage/tests/e2e`
for PostgreSQL concurrency, idempotency, precision, warnings and migration round
trips. Run root `make quality` and the backend unit lane before merging.
`make typecheck-critical` checks accounting tests separately from the backend's
general test exclusions.

For a configured provider, run `E2E_LLM_MODE=real uv run pytest
app/modules/usage/tests/e2e/test_real_model_accounting_e2e.py -s`. This opt-in test
uses disposable PostgreSQL and compares provider usage with persisted receipts
and independent price arithmetic. It verifies catalog-priced usage, not invoices.
