# Module contracts

What every API operation and every product event actually guarantees.

Three documents, three questions, and keeping them apart is what stops any of
them becoming a dumping ground:

| Document | Answers |
|---|---|
| [Product specification](../../../docs/product/README.md) | What a person can do, and what the system owes them |
| [Module guide](../modules/README.md) | What each module owns — its tables, its routes, its consumers |
| **These contracts** | What one operation or one event promises, precisely |

A scenario in the product specification says "a pod admin adds a member and they
get that role". The contract for `pod.member.add` says which permission is
required, what happens when the person is not in the organization, whether
calling it twice is safe, and which events it publishes.

## What goes in an entry

Per operation:

| Field | Content |
|---|---|
| Authorization | The permission required, and the grant path that satisfies it |
| Preconditions | What must be true, and what each violation returns |
| Effect | The state change on success, and whether repeating it is safe |
| Events | Domain events published, analytics events recorded |
| Errors | Status → condition |

Per event: who publishes it, which stream it lands on, which consumer groups
read it, whether it is delivered once or at least once, and what a consumer must
tolerate on redelivery. This is the part with no written contract before now,
and the part where the platform has actually been bitten — a consumer group lost
to a flush drops events silently, and nothing in the code says whose job it was
to notice.

## Generated, then gated

The tables are produced from the committed OpenAPI specification and the
analytics catalog:

```bash
uv run python scripts/check_contracts.py --write
```

Prose you write outside the generated block is preserved. `make quality` then
runs the check in both directions:

- a route or event with no contract entry fails the build, so a new endpoint
  lands with its contract rather than a promise to write one;
- a contract entry naming something that no longer exists also fails, so a
  deleted route takes its prose with it — stale prose is worse than none,
  because people believe it.

Same posture as the [route inventory](../modules/route-inventory.md) and the
OpenAPI freshness check, deliberately. It is one more generated-then-gated
document, not a new idea to learn.

## Status

The tables are complete: **263 entries covering every live operation and
event**. The prose is not — it is being filled in module by module, and an empty
entry is honest about that rather than pretending. Start with the module you are
changing.

## The modules

[agent](agent.md) ·
[agent_surfaces](agent_surfaces.md) ·
[apps](apps.md) ·
[connectors](connectors.md) ·
[datastore](datastore.md) ·
[function](function.md) ·
[icon](icon.md) ·
[identity](identity.md) ·
[pod](pod.md) ·
[pod_bundle](pod_bundle.md) ·
[schedule](schedule.md) ·
[usage](usage.md) ·
[workflow](workflow.md) ·
[workspace](workspace.md)

And the events, which belong to no single module: [events](events.md).
