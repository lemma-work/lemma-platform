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
`managed_bot_persistence.py:149` (a managed bot whose setup is not enabled).

*Messages* are dropped on both inbound routes. The shared platform endpoint goes
through `surface_repository.py:121` and `:174`, which filter
`status == AgentSurfaceStatus.ACTIVE`, so a disabled surface is never a
candidate; the surface-addressed endpoint reaches
`surface_inbound.py:352`, which returns `None` on `not surface.is_active`
before the payload is even parsed. Either way ingress yields no context, the
worker enqueues nothing, and no reply, conversation or message row is produced.

*Slack lifecycle events are not.* `webhook_ingest.py:300` calls
`AppEventHandler.try_handle_channel_setup` **in the HTTP request, before
anything is published** — because a `trigger_id` expires in about three seconds
— and `app_event_handler.py` consults `status` nowhere. So a disabled Slack
surface still opens its channel-setup modal.

Nothing in `lemma-frontend/src` mentions the status, so there is no way to see or
unset it from the product.
**Why it matters:** the platform side keeps working — the bot is still in the
channel, the number still receives — so a person messaging a disabled surface
sees their message delivered and simply never answered, indistinguishable from
the agent ignoring them, with no signal on either side. On Slack it is stranger
than that: the surface still opens configuration modals, so it answers clicks
and not words. Whatever `INACTIVE` is supposed to mean, it does not currently
mean one thing.
**Fix:** unknown, and that is the point of the entry. Three shapes are possible
and they are product decisions, not code ones: (a) `INACTIVE` should not exist —
deleting a surface is the way to stop it, and the flag is a half-built feature
worth removing; (b) it should exist and a disabled surface should *answer*, saying
it is switched off; (c) it should exist, stay silent, and gain UI so somebody can
see why nothing is happening. Decide before writing code.
**How it was found:** tracing `AgentSurfaceStatus.INACTIVE` from
`domain/entities.py:248` to its readers during the surfaces schema rework, then
grepping `lemma-frontend/src` for any reference to it and finding none.

### DEV-SURF-002 — A reassigned phone number signs in as the person who had it
**Violates:** nothing, *as written* — see **Required** below, which is the part
that needs deciding.
**Severity:** question
**Where:** `lemma-backend/app/modules/agent_surfaces/services/onboarding_transport.py:73`
(`platform_binding_key`) and
`lemma-backend/app/modules/agent_surfaces/services/onboarding_sender.py:195`
(`verified_sender`)
**Required:** PS-SURF-012 says the system "shall give a resolved person exactly
the access their Lemma identity has, and no more". It also says a message from
an external identity "shall resolve to a Lemma user where one exists" and that
the resolution "shall keep stable across later messages" — and those two
sentences are in tension the moment an identifier changes hands. Nothing in the
specification says an external identifier names one person for all time; it is
assumed, and this is the case where the assumption is wrong. Whether the second
bullet should be read as broken here is a product decision, which is why the
`Violates:` line above names nothing: marking PS-SURF-012 `gap` would say in
`coverage.md` that Lemma does not resolve people correctly, which overstates a
failure confined to a reassigned number.
**Actual:** `platform_binding_key` hashes `(platform, tenant, installation,
sender_external_user_id)`, and on WhatsApp the sender's external id *is* their
phone number. A recycled number therefore produces the same `binding_key` as the
previous holder's, so `verified_sender` finds their `VerifiedSurfaceIdentity`,
and the new holder is signed in as them.

The guard already there does not catch it. It revokes when
`identity.verified_phone` no longer matches the resolved user's
`mobile_number`, or when that number is no longer verified — which covers the
previous holder *changing* their number. It does not cover them keeping it in
their profile while the carrier gives it to somebody else, and there is no
inbound signal that says so: Meta's Cloud API reports no reassignment.

Telegram is not exposed the same way (the sender id is an account id, not a
reassignable identifier), and neither is Slack or Teams. The
`telegram_username` path is a different shape of the same problem and is
already refused for binding by `_cache_is_attested`.
**Why it matters:** the new holder of the number reaches the previous holder's
workspaces, conversations and pod content, having proved nothing. It needs no
attacker — carriers reassign numbers routinely, and in several countries within
months. The blast radius is whatever that account could reach.
**Fix:** unknown, and every option is a product decision about how much friction
to add to the common case. (a) Expire a verified identity after a period of
inactivity and make the next message re-verify — needs a number, and the number
is the whole trade. (b) Re-verify on a change of some observable the platform
does give us, if one can be found that moves on reassignment. (c) Accept it,
write it down as accepted, and give an owner a way to revoke a binding when
somebody reports it. Decide before writing code.
**How it was found:** an adversarial pass over chat signup during the WhatsApp
number-pool work, tracing what `binding_key` is actually made of and then
checking each guard in `verified_sender` against a number that changes hands
rather than a person who changes number.
