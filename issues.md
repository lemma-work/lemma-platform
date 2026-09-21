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

**What already bounds it**, and it is worth knowing before choosing a fix: one
revocation trigger exists and fires on exactly the right event.
`UserEntity.update` clears `mobile_verified_at` and raises
`UserMobileChangedEvent` whenever the digits change, and
`agent_surfaces/events/handlers.py:375` revokes every phone-bound
`VerifiedSurfaceIdentity` that is no longer the account's number — the whole lot
when the account has no verified number left. So the window closes by itself the
moment the previous holder puts their new number in Lemma, which somebody who
has moved on to a new number usually does.

It stays open indefinitely for the case where they never come back: an abandoned
account keeps the old number in its profile, and nothing else revokes. That is
the population the fix is actually for, and it is smaller than the entry first
implied.
**Not introduced here.** `onboarding_sender.py` -- which holds `verified_sender`
and its guards -- is byte-identical to `origin/main`, and `platform_binding_key`
is unchanged too. This is a property of the shipped product that an adversarial
pass over the WhatsApp pool work happened to surface, not a regression the pool
brought with it. It is recorded here because it was found here.

**Fix:** unknown, and every option is a product decision about how much friction
to add to the common case. There is also no house convention to follow: the TTLs
this codebase has -- `PendingChatOnboarding.expires_at`, the email challenges --
are all on *pending* artifacts, things waiting to be completed. Nothing expires a
proof that already succeeded, so a period chosen here would be a new policy
rather than consistency with an old one, and that is precisely the call this
entry is holding open. (a) Expire a verified identity after a period of
inactivity and make the next message re-verify — needs a number, and the number
is the whole trade. (b) Re-verify on a change of some observable the platform
does give us, if one can be found that moves on reassignment. (c) Accept it,
write it down as accepted, and give an owner a way to revoke a binding when
somebody reports it -- the cheapest of the three, because the revocation itself
already exists and only a trigger is missing: nothing but a profile edit by the
previous holder can fire it today. Decide before writing code.
**How it was found:** an adversarial pass over chat signup during the WhatsApp
number-pool work, tracing what `binding_key` is actually made of and then
checking each guard in `verified_sender` against a number that changes hands
rather than a person who changes number.

### DEV-SURF-003 — A hand-written bundle cannot name an agent's mailbox
**Violates:** nothing written down. No statement says what a bundle's named
mailbox means for an agent that already has one.
**Severity:** question
**Where:** `lemma-backend/app/modules/pod_bundle/infrastructure/surface_apply.py:123`
and `lemma-backend/app/modules/agent_surfaces/services/credential_uniqueness.py:127`
**Required:** unwritten, and that is the finding. `PS-PACK-012` says an import
"either finishes or can be safely retried"; it does not say what happens when
the bundle declares a thing the schema forbids a second of.
**Actual:** every agent is given a mailbox as it is created, `agent_id` is
`NOT NULL`, and `uq_agent_surface_agent_type` is unique on
`(agent_id, surface_type)` — so an agent holds at most one Resend surface. The
applier looks for an existing surface *by name*
(`surface_apply.py:113`), and a bundle naming its mailbox anything other than
the auto-minted `surface_name_for(agent_name)` finds none, takes the create
path, and reaches `ensure_one_surface_per_agent`, which raises
`AgentSurfaceAgentPlatformConflictError` — a 409 naming a surface whoever ran
the import never created.

Measured through the real applier, and the first version of this entry was
wrong about the scope. `test_what_the_bundle_applier_does_with_an_email_surface`
builds a `BundleApplier` the way `pod_bundle/events/handlers.py` does and calls
`apply_step` against a real schema on a deployment where email is configured:

* **A round trip lands.** `surface_name_for` is `resend-{slugify(agent_name)}`
  with nothing random in it, and the exporter writes a surface under the name it
  actually has — so a bundle exported from a pod carries `resend-reporter`, the
  imported agent `Reporter` is given a mailbox of exactly that name, and the
  applier's lookup finds it and updates. "A bundle with an email surface cannot
  be imported" was the claim, and it is false.
* **Any other name is refused**, with
  `AGENT_SURFACE_AGENT_PLATFORM_CONFLICT` naming the auto-minted surface. So the
  real population is a hand-written bundle, or one whose agent was renamed
  between export and import.

That claim was wrong because it was read rather than run, twice over. The call
chain was traced correctly and the applier's own name lookup was missed; then a
first test posted to `/pods/{id}/surfaces`, which has no such lookup, and drew a
bundle conclusion from a controller's 409. The controller's behaviour is real
and pinned separately by
`test_a_named_mailbox_for_an_agent_that_has_one_is_refused` — a named connect
there always loses, even when the name it asks for is the one the mailbox
already has.

The reason none of this was known is the rest of the finding:
`test_importing_a_named_surface_leaves_the_agent_s_mailbox_alone` drives a
`FakeSurfaceService` that enforces no unique index, no bundle fixture declared a
`RESEND` surface, and no scenario imports one — both verified by grep.
**Why it matters:** not for round trips, which work. For anyone writing a bundle
by hand, or re-importing one after renaming its agent: the failure arrives as a
409 about a surface they did not write and cannot see in the bundle, and the
message tells them to "pick another agent" when what they need to do is name the
mailbox `resend-{agent}`.
**Fix:** three shapes, all product decisions. (a) The applier adopts the agent's
mailbox and renames it to the bundle's name — needs `update_surface` to accept a
name, which it does not today. (b) The exporter writes the mailbox under the
name it actually has, so a round-trip matches and a hand-written bundle is told
to do the same. (c) It is refused, but with an error that says an agent has one
mailbox and names the bundle's own surface rather than the auto-minted one.
Whichever is chosen, the test needs to run against a real schema.
**How it was found:** widening the Resend adoption to named requests to fix four
failing scenarios, having reasoned that the unique index left only one candidate
a name could mean. CI's unit lane — wider than the local `-m unit` lane —
failed on the bundle test, which is what made the bundle path visible at all.
The widening was reverted; this is what it had walked into.
