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

### DEV-SURF-005 — A new pod is already connected to a surface
**Violates:** PS-SURF-001
**Severity:** question
**Where:** [`pod_service.py`](lemma-backend/app/modules/pod/services/pod_service.py):97

**Required:** PS-SURF-001's scenarios open by asserting a new pod is connected
to nothing, then connect one and check what changed. "A person connects a pod's
agent to a platform" reads as something the person does.

**Actual:** Creating a pod mints the assistant's mailbox, so
`agent.surface.list` returns one `resend` surface immediately. Two scenarios
fail on the precondition rather than on what they set out to prove:
`test_available_platforms_are_listed` and `test_an_unconfigured_surface_is_refused`
(both in `tests/scenarios/journeys/surfaces_and_notifications/test_surfaces.py`).

This is a question rather than a bug because the behaviour looks deliberate and
good — an agent with no other way to reach anyone should have an address, and
`create_agent` has minted one for a while. What is unresolved is whether "a
person connects a surface" is still the right framing for the *first* one, or
whether the promise should say every pod starts with an address and connecting
is about the rest. The scenarios cannot be edited to match either way until that
is decided; that is what makes it a spec question.

**Found:** running `tests/scenarios/journeys/surfaces_and_notifications` against
a local deployment. Predates the surface-delivery work: minting at pod creation
arrived in dcae7d88 (#494) and nothing in that branch touches `pod_service.py`.

