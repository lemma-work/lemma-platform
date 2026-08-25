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

### DEV-DATA-004 — Removing a column makes the table unreadable until the connection recycles
**Violates:** `PS-DATA-002`
**Severity:** high
**Where:** [`db/session.py:33-52`](lemma-backend/app/core/infrastructure/db/session.py#L33)
— `_build_connect_args` sets `server_settings` and nothing else. Neither engine
passes `statement_cache_size`, and no handler for asyncpg's
`InvalidCachedStatementError` exists anywhere in the codebase.

**Required:** A table's shape can change without losing what is in it. Somebody
removes a column they no longer want and reads the table.

**Actual:** The read answers **400**, body `cached statement plan`. Exactly
reproducible, and the order matters:

```
create table (subject, body); add a record
add column `priority`  -> read OK, priority is null on the existing row
remove column `body`   -> read 400  <- here
```

**Why:** asyncpg keeps a per-connection prepared-statement cache. Dropping a
column invalidates the cached plan for the statement that reads that table, and
the next borrower of that pooled connection gets the driver error rather than
rows. Nothing catches it, so it leaves as a 400.

**Why it matters:** the connection is *pooled*, so the damage is not confined to
whoever ran the DDL. Any request that lands on that connection sees the table as
broken, and it stays that way until the connection is recycled (`pool_recycle`,
300s on the datastore engine). A person who removes a column sees their table
stop working, then start working again, with nothing they did in between — which
is close to unreportable.

**Not established:** why the `add` case survives and only `remove` fails. The
run above shows the read after `add column` succeeding, but both are DDL against
the same relation, so the difference may be which pooled connection each request
happened to land on rather than anything about the operation. Worth settling
before choosing the fix, because "invalidate on drop" would be the wrong shape
if add is equally affected and merely luckier.

**Fix:** the two standard shapes are `statement_cache_size=0` in `connect_args`
(simple, costs the cache everywhere) or catching `InvalidCachedStatementError`
and retrying once on a fresh statement (keeps the cache, and is what the error
is for). Choosing between them wants the answer to the paragraph above.

**Found by:**
[`test_adding_and_removing_columns_keeps_the_records`](tests/scenarios/journeys/working_with_data/test_tables_and_records.py),
against dev. It passes against a locally booted stack, where the pool is small
and short-lived enough that the poisoned connection is rarely the next one
borrowed — which is why a deployment run found it and 400+ local scenarios did
not.

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

### DEV-SURF-004 — A person's default surface governs where they are answered, not where they are reached
**Violates:** PS-SURF-023
**Severity:** question
**Where:** [`notification_delivery.py`](lemma-backend/app/modules/agent_surfaces/services/notification_delivery.py):33,
[`notification_channels.py`](lemma-backend/app/modules/agent_surfaces/services/notification_channels.py):113

**Required:** "Where a person has chosen a default surface, the system shall
reach them there when it starts the contact, whatever platform any earlier
conversation used." Starting the contact is what a notification does.

**Actual:** Delivery never reads the preference. Candidates are the *sending
agent's* surfaces, ranked: the surface the run is already on, then its other
chat surfaces by freshest inbound, then its mailbox. The preference is read only
by inbound routing (`surface_routing._default_surface`) and by surface
configuration authorization.

The deeper mismatch is in the mechanism the promise names.
[`UserPreferences.default_surfaces`](lemma-backend/app/modules/identity/domain/user_preferences.py):12
maps *platform → surface id*, and exists so that one external identity resolving
to several pods lands in the pod the person meant. There is no cross-platform
"reach me here" for the outbound path to honour, so the clause "whatever platform
any earlier conversation used" describes a preference nobody can express.

**Why it matters:** Someone who sets a default expects proactive messages to
arrive there. They arrive instead on whichever of the sending agent's surfaces
they last wrote to — and the setting that looks like it controls this controls
something else, which is worse than having no setting.

**Fix:** A product decision before code. Either amend PS-SURF-023 to scope the
default to inbound pod disambiguation, and say plainly that agent-initiated
contact is ranked by sender identity — the identity argument in
`notification_delivery`'s docstring is the case for it — or add a real outbound
preference and give it precedence over freshness, below an explicit `channel`
and above the ranking.

**Found by:** reading delivery against
[`surfaces-and-notifications.md`](docs/product/journeys/surfaces-and-notifications.md)
while adding `message_user`'s `channel` argument, which widens the same gap: the
sending agent can now name a channel outright, and the recipient still cannot.
