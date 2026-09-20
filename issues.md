# Issues

Bugs, unexpected behaviour, and places where the implementation does not deliver
what [the product specification](docs/product/README.md) says it should.

Tracked in git on purpose. Each entry is something that was found once,
verified against the code, and understood — writing it down is what stops it
being rediscovered from scratch later. A finding here is not a plan or a
roadmap: it is a statement about how the system behaves today, with a citation.

**Every entry is verified by reading the code or by running against it, never
inferred from a route name or a test name.** Each one cites `file:line`, and
says how it was found.

When a finding is fixed, delete its entry in the pull request that fixes it. A
register of already-fixed bugs is worse than no register — it teaches people to
stop trusting the file.

Ids are stable and append-only, so a `DEV-` reference in a scenario, a commit
message, or a code comment resolves to something.

## Format

```
### DEV-<AREA>-<NNN> — one-line summary
**Violates:** PS-<AREA>-<NNN>
**Severity:** high | medium | low | question
**Where:** path:line
**Required:** what the spec says must happen.
**Actual:** what happens instead.
**Why it matters:** the user-visible consequence.
**Fix:** the shape of the change.
```

Severity `question` means the divergence may be deliberate and the spec may be
the thing that is wrong — resolve it with a product decision before writing code.

## Open

### DEV-SURF-001 — A disabled surface drops every message to it, in silence
**Violates:** nothing — and that is the finding. No statement defines what a
*disabled* surface does with an inbound message.
**Severity:** question
**Where:** `lemma-backend/app/modules/agent_surfaces/infrastructure/repositories/surface_repository.py:121`
and `:174`
**Required:** Unwritten. The nearest principle in the specification is the one
PS-SURF-012 applies to a person the system will not answer: tell them how to get
access "rather than failing silently". Whether that principle should extend to a
surface somebody switched off has never been decided.
**Actual:** `AgentSurfaceStatus.INACTIVE` is reachable from three write paths —
`surface_controller.py:287` (creating with `is_enabled: false`),
`surface_controller.py:405` (patching `is_enabled`), and
`managed_bot_persistence.py:149` (a managed bot whose setup is not enabled). Both
routing reads filter `status == AgentSurfaceStatus.ACTIVE`, so a disabled surface
matches no candidate. The webhook is still live, still verifies the signature, and
still returns 200; the message then routes to nothing and is discarded with no
reply and no record. Nothing in `lemma-frontend/src` mentions the status, so
there is no way to see or unset it from the product.
**Why it matters:** the platform side keeps working — the bot is still in the
channel, the number still receives — so a person messaging a disabled surface
sees their message delivered and simply never answered. It is indistinguishable
from the agent ignoring them, and the deployment has no signal either.
**Fix:** unknown, and that is the point of the entry. Three shapes are possible
and they are product decisions, not code ones: (a) `INACTIVE` should not exist —
deleting a surface is the way to stop it, and the flag is a half-built feature
worth removing; (b) it should exist and a disabled surface should *answer*, saying
it is switched off; (c) it should exist, stay silent, and gain UI so somebody can
see why nothing is happening. Decide before writing code.
**How it was found:** tracing `AgentSurfaceStatus.INACTIVE` from
`domain/entities.py:248` to its readers during the surfaces schema rework, then
grepping `lemma-frontend/src` for any reference to it and finding none.
