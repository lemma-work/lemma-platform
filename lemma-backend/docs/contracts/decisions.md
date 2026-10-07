# decisions contract

What every `decisions` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `decision.make` | POST | `/pods/{pod_id}/decisions` | Make a decision |

<!-- /generated:operations -->

## `decision.make`

Answers closed questions about one piece of evidence and stores nothing.

- **Who:** a member of the pod, or a function or agent acting in it with a
  delegated token. The decision is attributed to the person; a workload is
  tagged as the workload. A delegated token for another pod is refused (403).
- **Before:** the questions are a flat JSON Schema object of choice,
  multi-choice, yes/no and integer-scale properties, each with a `description`;
  the evidence is at most 64 KiB and the examples at most 20 totalling 32 KiB.
  Anything else is a 422 listing every problem (`DECISION_INVALID_REQUEST`, or
  `DECISION_INPUT_TOO_LARGE` when a cap was the problem). A body over 512 KiB is
  a 413 before it is read.
- **Changes:** nothing but the organization's per-minute counter and the usage
  ledger, where the call is metered like a model call.
- **Answers:** one per question, `{value, confidence}`. `value: null` means the
  evidence did not support an answer; `confidence` is null unless the provider
  measures one.
- **Refusals:** 429 `DECISION_RATE_LIMITED` with `Retry-After` when the
  organization is over its rate; 429 `USAGE_LIMIT_EXCEEDED` when spend has run
  out; 503 `DECISION_PROVIDER_UNAVAILABLE` (`details.reason`: `timeout`,
  `transport`, `provider_error`, `invalid_output`, `not_configured`) when the
  provider did not answer. 429 and 503 are worth retrying; a 422 is not.
