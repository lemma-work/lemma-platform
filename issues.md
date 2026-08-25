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

---

## SURF — surfaces and notifications

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

Found by reading delivery against this journey while adding `message_user`'s
`channel` argument, which widens the same gap: the sending agent can now name a
channel outright, and the recipient still cannot.

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
