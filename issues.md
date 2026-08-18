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

## ONB — signup, organizations, invitations

### DEV-ONB-001 — An organization can be left with no owner, permanently
**Violates:** PS-ONB-041
**Severity:** high
**Where:** [`organization_service.py:636`](lemma-backend/app/modules/identity/services/organization_service.py#L636)
(`remove_member`), [`:612`](lemma-backend/app/modules/identity/services/organization_service.py#L612)
(`update_member_role`)

**Required:** An organization always has at least one owner. Any operation that
would remove the last one is refused.

**Actual:** Two paths reach zero owners, neither guarded:

1. `remove_member` sets `is_self = member.user_id == requester_user_id` and,
   when true, skips every role check before calling `delete_member`. The sole
   owner can remove themselves.
2. `update_member_role` requires the requester be `ORG_OWNER`, then applies the
   new role with no further check. The sole owner can demote themselves to
   `ORG_MEMBER`.

**Why it matters:** The state is unrecoverable through the API. Once there are
no owners: `update_organization` requires `ORG_OWNER` (`:195`),
`update_member_role` requires `ORG_OWNER` (`:629`), and `can_grant_org_role`
([`organization_entities.py:31`](lemma-backend/app/modules/identity/domain/organization_entities.py#L31))
caps every non-owner at granting `ORG_MEMBER`. So no remaining member can ever
mint an owner. The organization can still be read and used, but can never again
be administered — its name, joining rules, and member roles are frozen forever.
Reachable by one ordinary API call from a normal user, with no warning.

**Fix:** Guard both paths. Count remaining `ORG_OWNER` members before removal or
demotion and refuse with a 409 when the count would reach zero. The self-removal
branch needs the check too — it is the easier of the two to hit, since "leave
organization" reads as harmless.

---

### DEV-ONB-002 — Organization display names are unique across the whole deployment
**Violates:** PS-ONB-014
**Severity:** question
**Where:** [`organization_service.py:147`](lemma-backend/app/modules/identity/services/organization_service.py#L147)

**Required:** The handle (slug) is unique. The display name is not — two
unrelated organizations may both be called "Acme".

**Actual:** `create_organization` calls `get_by_name(entity.name)` with no tenant
scope and raises `NAME_TAKEN` on any match anywhere in the deployment.
`update_organization` (`:206`) applies the same rule on rename.

**Why it matters:** Two things, one product and one privacy:

- On a shared deployment, the first customer to register a common name takes it
  from everyone else. There is no recovery and no explanation the second
  customer would accept.
- The 409 is an existence oracle. Anyone can enumerate which organization names
  exist on the deployment by trying to create them — including names that
  confirm a competitor or a customer is present. `is_name_available` (`:231`)
  exposes the same oracle directly as an endpoint.

**Fix:** Decide first — this may be deliberate for a single-tenant or invite-only
deployment, in which case PS-ONB-014 is what needs changing. If it is not
deliberate, drop the uniqueness check on name, keep it on slug, and remove or
scope `is_name_available`.

---

### DEV-ONB-003 — Accepting a pod invitation can silently grant only the organization
**Violates:** PS-ONB-021
**Severity:** medium
**Where:** [`organization_service.py:512`](lemma-backend/app/modules/identity/services/organization_service.py#L512)
(`accept_invitation`)

**Required:** When an invitation names a pod, accepting it grants both
organization membership and pod membership. If the pod cannot be granted, the
acceptance fails and says why.

**Actual:** The pod grant is attempted only when
`invitation.pod_id is not None and self.pod_membership_port is not None`, and
inside that, only when `get_pod_organization_id(...)` returns non-`None`. Both
fall-throughs are silent: the member row is already persisted and the invitation
already marked accepted, so the call returns 200 with the pod quietly dropped.

**Why it matters:** The invitation the person received named a pod — that is
usually the entire reason they accepted. They land in the organization, cannot
see the pod, and nothing in the response indicates anything went wrong. The
invitation is now `ACCEPTED` and cannot be replayed, so recovery needs an admin.

**Fix:** Make the pod grant part of the same transaction and fail the acceptance
if the pod named by the invitation cannot be resolved. A pod that has been
deleted since the invitation was sent is a real case — that one should fail with
a message naming the pod, not succeed silently.

**Scope, measured:** the ordinary path works. A scenario that invites somebody
to a pod and accepts finds them in the pod, so `pod_membership_port` is wired in
a standard deployment and the grant lands. What is not guarded is the
fall-through: invite, delete the pod, accept — that returns 200 with the pod
dropped and the invitation spent. `test_an_invitation_to_a_vanished_pod_is_not_silently_half_applied`
holds it, marked `xfail(strict=True)`.

### DEV-ONB-004 — Every organization member-join crashes the analytics consumer
**Violates:** PS-ONB-020
**Severity:** high
**Where:** [`analytics_consumer.py:291`](lemma-backend/app/composition/analytics_consumer.py#L291)

**Required:** When someone joins an organization, the system records
`organization.member_joined`.

**Actual:** The consumer calls a method that does not exist:

```python
members = await OrganizationRepository(uow).list_members(
    parsed_member.organization_id
)
```

`OrganizationRepository` has `get_member`, `get_member_by_id`,
`get_member_by_email` and **`list_organization_members`** — no `list_members`.
Every `identity.organization.member_added` event therefore raises
`AttributeError`, goes through `_retry_or_dead_letter`, and is re-raised.

Caught by running the scenario suite: the worker log shows the traceback on an
ordinary invite-and-accept, which is the most common write in the product.

**Why it matters:**

- `organization.member_joined` is **never recorded**, so the org-growth funnel
  in [product analytics](docs/design/product-analytics.md) is permanently
  empty and reads as "nobody ever joins an organization".
- Every join burns the event's full retry budget and lands in the dead-letter
  path, so the reliability signal is polluted by a guaranteed failure.
- It is pure noise in production error logs, at the rate people join
  organizations.

**Fix:** More than a rename — `list_organization_members` returns
`(members, next_cursor)` and caps at `limit=100`, so `len(members)` on its
result would be `2` rather than the member count, and would be wrong past 100
members anyway. What the call site actually wants is a count. Add a
`count_members(organization_id) -> int` to the repository and use it; the
consumer only needs the number to bucket it.

**Also worth noting:** the same log shows `analytics.actor.unattributed` for
`organization.created`, emitted with `actor_type: SYSTEM`. Worth a look while
in this file — an event about a person creating something should not be
attributed to the system.

---

## POD — pods, pod membership, pod roles

### DEV-POD-001 — An org editor can open any pod but cannot see that it exists
**Violates:** PS-POD-030
**Severity:** medium
**Where:** [`pod_service.py:94`](lemma-backend/app/modules/pod/services/pod_service.py#L94)
(`get_pod`) vs [`:194`](lemma-backend/app/modules/pod/services/pod_service.py#L194)
(`list_pods_by_organization`)

**Required:** What a person can open and what a person can list are the same
set. Access granted by an organization role applies consistently to both.

**Actual:** The two disagree about `ORG_EDITOR`:

- `get_pod` returns the pod when
  `org_member.role in [ORG_OWNER, ORG_EDITOR]`, with no pod-membership check.
- `list_pods_by_organization` takes the org-wide listing branch only when
  `org_member.role == ORG_OWNER`; an editor falls through to
  `list_by_org_member` and sees only pods they belong to.

An org editor therefore has full read access to every pod in the organization
by id, while the product shows them only their own. `delete_pod` (`:168`) uses
the owner-only rule too, so the editor's elevated access is read-shaped and
inconsistent with both neighbours.

**Why it matters:** Whichever side is right, the other is a bug, and they fail
in opposite directions. If listing is correct, `get_pod` is an access-control
hole — an editor reads pods nobody intended them to see, and the UI never
reveals it. If `get_pod` is correct, the product is hiding pods from someone
entitled to them. It cannot be discovered by using the app, because the pod is
absent from every list that would show it.

**Fix:** Pick one and apply it in all three places. Least privilege says drop
`ORG_EDITOR` from the `get_pod` branch, matching listing and deletion — an
editor then reaches pods through pod membership like everyone else.

---

### DEV-POD-002 — A pod can be left with no admin
**Violates:** PS-POD-041
**Severity:** low
**Where:** [`pod_member_service.py:275`](lemma-backend/app/modules/pod/services/pod_member_service.py#L275)
(`remove_member_from_pod`), [`:353`](lemma-backend/app/modules/pod/services/pod_member_service.py#L353)
(`update_member_roles`)

**Required:** A pod always has at least one admin, for the same reason an
organization always has at least one owner.

**Actual:** No path counts remaining admins before removing a member or
replacing their roles.

**Why it matters:** Materially less than the organization case, and the
difference is worth stating so this is not fixed with the same urgency:
`remove_member_from_pod`, `update_member_roles`, and `delete_pod` all bypass the
pod-role check entirely for an `ORG_OWNER`. An adminless pod is therefore always
recoverable — an org owner can appoint a new pod admin at any time. The failure
is an annoyance needing escalation, not the permanent lockout of `DEV-ONB-001`.

**Fix:** Same guard as `DEV-ONB-001`, lower priority. Refuse the removal or role
change that would zero the admin count, with the message pointing at appointing
a replacement first.

---

### DEV-POD-004 — A typo in a permission id answers 500 with a stack trace
**Violates:** PS-POD-013
**Severity:** medium
**Where:** [`service.py:242`](lemma-backend/app/core/authorization/service.py#L242)
raises; [`pod_role_controller.py:103`](lemma-backend/app/modules/pod/api/controllers/pod_role_controller.py#L103)
calls it without translating.

**Required:** Naming a permission that does not exist is a mistake in the
request, so the system says which id was wrong and answers 4xx.

**Actual:** `POST /pods/{id}/roles` with `permission_ids: ["pod.member.remove"]`
— a plausible near-miss for the real `pod.member.manage` — answers **500** with
a Starlette traceback in the body.

The validation itself is already right, and the message it produces is exactly
what the caller needs:

```python
unknown = set(permission_ids) - set(PERMISSION_BY_ID)
raise ValueError(f"Unknown permission id(s): {', '.join(sorted(unknown))}")
```

Nothing maps that `ValueError` to a status, so it escapes the controller as an
unhandled exception and the good message is thrown away with it.

**Why it matters:** Building a custom role means typing permission ids by hand,
so this is on the ordinary path rather than an edge — and a 500 tells the person
the platform is broken when what actually happened is that they made a typo the
platform had already diagnosed.

What the client gets back depends on `DEBUG`. The application registers a clean
`INTERNAL_ERROR` JSON handler for unhandled exceptions, but Starlette's
`ServerErrorMiddleware` returns its own HTML traceback *instead of* calling that
handler whenever `debug` is true — and `debug` defaults to true. So on a default
deployment this answers with a stack trace, which the logging contract says API
responses never do. See `DEV-OPS-006`.

**Fix:** Catch `ValueError` in the two controller paths that call
`create_or_update_role` and re-raise as a 400 carrying the message. The same
escape exists on `pod.roles.update`.

**Covered by:** `test_an_unknown_permission_is_refused_clearly`, marked
`xfail(strict=True)`.

---

## DATA — tables, records, files

### DEV-DATA-001 — Supplying markdown for an un-indexed document is accepted and discarded
**Violates:** PS-DATA-040
**Severity:** medium
**Where:** `file.markdown.attach` —
[`file_controller.py`](lemma-backend/app/modules/datastore/api/controllers/file_controller.py)

**Required:** Where a person supplies their own markdown for a document, the
system uses it in place of what it would have extracted. If it cannot, it says
so.

**Actual:** Whether the supplied text is kept depends on a flag set at upload
time, and the response does not distinguish the two:

| upload `search_enabled` | attach | file status | children stored |
|---|---|---|---|
| `true` | 200 | `COMPLETED` | 1 (`document.md`) |
| `false` | **200** | `NOT_REQUIRED` | **0** |

With indexing off the call answers success and nothing is stored — no child, no
retrievable content, no error, no changed status.

**Why it matters:** Supplying markdown is the escape hatch for a document the
platform cannot extract — a scanned PDF, an unusual format, or a deployment with
no extraction service at all (which is what this suite's stack is, and how the
behaviour was found). It is exactly the path a person reaches for when
extraction has already let them down, and it fails silently. They get a 200,
believe the document is readable, and discover otherwise when an agent cannot
answer questions about it.

**Fix:** Either refuse the attach when the file is not indexed, naming the
reason, or accept it and store the child regardless — supplied markdown does not
need an extraction pipeline to be worth keeping. Refusing is the smaller change;
accepting is the better product. What must not remain is 200 with nothing kept.

---

### DEV-DATA-002 — One of four pod-schema creators skips the lock that exists for it
**Violates:** *(no promise — the retry hides it from every client. This is an
internal invariant the code states and then breaks in one place.)*
**Severity:** medium
**Where:** [`postgres_search_service.py:83`](lemma-backend/app/modules/datastore/services/search/postgres_search_service.py#L83),
against [`schema_manager.py:148`](lemma-backend/app/modules/datastore/infrastructure/schema_manager.py#L148)
(`_lock_schema_bootstrap`)

**Required:** Creating a pod's datastore schema is safe against a concurrent
creator. The code already knows this and says so in the lock's own docstring:

> PostgreSQL's `CREATE SCHEMA IF NOT EXISTS` is not race-free: two concurrent
> transactions can both observe the namespace as absent and one later fails the
> `pg_namespace.nspname` unique index. […] so they must share this
> transaction-scoped advisory lock.

**Actual:** Four places run `CREATE SCHEMA IF NOT EXISTS "pod_…"`. Three take
`pg_advisory_xact_lock(hashtext(schema_name))` first. The search service's
`_ensure_schema` does not — it builds the same name (`pod_{uuid}` at `:44`) and
creates it bare.

Observed rather than reasoned: a scenario run's worker log carries

```
IntegrityError: duplicate key value violates unique constraint
  "pg_namespace_nspname_index"
DETAIL:  Key (nspname)=(pod_01a015d9_51d1_75e0_aa86_4553f099eeca) already exists.
[SQL: CREATE SCHEMA IF NOT EXISTS "pod_01a015d9_51d1_75e0_aa86_4553f099eeca"]
```

raised from `pod_schema_consumer.on_pod_created` — the locked path losing to the
unlocked one.

**Why it matters:** The pod-created consumer fails and retries, so the schema
does arrive; the cost is a guaranteed error and burnt retry budget whenever a
pod's first search initialisation overlaps its provisioning. It also puts a real
`IntegrityError` in the log for a condition that is not a bug in the caller,
which is how genuine integrity failures stop being noticed. A pod created and
immediately used — the common shape for anything scripted, and for bundle
import — is exactly the overlap.

**Fix:** Take the same lock in `_ensure_schema`, or route it through
`SchemaManager.create_datastore_schema`, which already does. The second is
better: it keeps one creator, and the search service currently also misses the
schema-level `try_grant` that `create_datastore_schema` does afterwards.

---

## FLOW — workflows

### DEV-FLOW-001 — Both workflow visualisation endpoints return 500, always
**Violates:** PS-FLOW-002
**Severity:** high
**Where:** [`workflow_controller.py:381`](lemma-backend/app/modules/workflow/api/workflow_controller.py#L381)
(`workflow.visualize`),
[`workflow_run_controller.py:415`](lemma-backend/app/modules/workflow/api/workflow_run_controller.py#L415)
(`workflow.run.visualize`)

**Required:** A person can see the shape of a workflow, and the path a run
actually took.

**Actual:** Both endpoints raise before rendering:

```
TypeError: cannot use 'tuple' as a dict key (unhashable type: 'dict')
```

Both call Starlette's templating with the signature that was removed:

```python
return templates.TemplateResponse(
    "workflow_view.html",
    {"request": request, "workflow": workflow.model_dump(mode="json")},
)
```

Starlette (**1.6.0** at the time of writing; the call has been broken across
several versions) takes `(request, name, context)`. The two-argument form is no
longer the supported call, and the failure is inside the compatibility handling
rather than a clean `TypeError` on the signature — which is why it reads as a
data problem rather than an API change. Check the pinned version with
`uv run python -c "import starlette; print(starlette.__version__)"` rather than
trusting this line.

Found by a scenario doing nothing exotic: create a workflow, ask to see it.
There is no branch on graph content before the template call, so this fails for
every workflow and every run, not only empty ones.

**Why it matters:** Visualisation is how a person understands a workflow they
did not write and how they debug a run that went the wrong way — the two moments
a graph engine most needs to be legible. Both are dead. **No test covers either
endpoint** (`grep -rn "visualize" --include="test_*.py" app/` returns nothing),
which is why a dependency bump could remove them silently.

**Fix:** Move both to the supported signature:

```python
return templates.TemplateResponse(
    request, "workflow_view.html", {"workflow": workflow.model_dump(mode="json")}
)
```

Then add a scenario for each — they are one line apiece and would have caught
this at the bump.

---

## ACCESS — grants and delegation

### DEV-ACCESS-001 — A removed pod member keeps full control of their agent conversations
**Violates:** PS-ACCESS-023, PS-POD-040
**Severity:** high
**Where:** `ConversationService.get_conversation` →
[`conversation_service.py:285`](lemma-backend/app/modules/agent/services/conversation_service.py#L285)
(`_validate_conversation_access`) and the `AGENT_READ` grant check at
[`:294`](lemma-backend/app/modules/agent/services/conversation_service.py#L294)

**Required:** When a person is removed from a pod, everything in that pod
closes to them on their next request — including the conversations they
started and the agents they were driving.

**Actual:** The pod closes; the conversation does not. Removal is applied
correctly at the pod level and then ignored one layer down:

```
DELETE /pods/{pod}/members/{member}                 -> 204
GET    /pods/{pod}                                  -> 403   (correct)
GET    /pods/{pod}/conversations/{id}               -> 200   (leaks)
POST   /pods/{pod}/conversations/{id}/messages      -> 200   (acts)
```

**Why it matters:** This is the departing-employee case, and it is the worst
shape it could take. Reading the old thread is a disclosure; the second line is
worse — the removed person can still *send the agent new instructions*, and the
agent executes them with the grants it holds in a pod its operator has already
been thrown out of. Removing someone is the one control an admin has when
access must stop now, and for agent work it does not stop it at all.

The cause is that conversation access is decided by ownership plus an
`AGENT_READ` grant, and both survive the membership that produced them. The
creator's grant on the agent they made is never dropped, so
`_require_agent_action` keeps saying yes. `PS-POD-040` promises exactly this
does not happen ("shall also drop the resource grants they held through that
membership"), and the existing scenario for it only checks `pod.get` — which is
why nothing caught it.

**Fix:** Make pod membership a precondition of conversation access rather than
a thing inferred from grants: check it in `_validate_conversation_access`, where
`pod_id` is already in hand. Dropping creator grants on removal is the deeper
fix and is worth doing as well, but the membership check is what closes the hole
in every module at once rather than one resource type at a time.

**Covered by:** `test_removing_a_person_stops_their_delegations`, marked
`xfail(strict=True)` — it turns the build red the moment this is fixed.

### DEV-ACCESS-002 — A second approval for the same action is answered "yes" and thrown away
**Violates:** PS-ACCESS-022, PS-AGENT-020
**Severity:** medium
**Where:** `_run_if_exact_match_already_approved` →
[`pydantic_adapter.py:252`](lemma-backend/app/modules/agent/tools/user_interaction/pydantic_adapter.py#L252),
which returns before
[`record_session_approvals`](lemma-backend/app/modules/agent/services/approval_reconciliation.py#L162)
can run.

**Required:** When a person approves an action for the session, the permissions
that approval names are remembered for the rest of the conversation.

**Actual:** Only the first approval of a given call is remembered. A second
`request_approval` naming the same tool and the same arguments takes the
exact-match fast path: it executes the call as the user, reports
`"Auto-approved: you approved this exact call earlier"` — and never records the
`permission_ids` it was carrying, because resolution is the only thing that
records them and resolution is exactly what was skipped.

Reading a table needs two permissions and the check stops at the first missing
one, so this is the ordinary path, not an edge:

```
pod_get_records            -> denied: datastore.table.read
request_approval  (ids=[table.read])   -> approved for session, executed
pod_get_records            -> denied: datastore.record.read     # past the table check
request_approval  (ids=[record.read])  -> "Auto-approved", executed, ids DISCARDED
pod_get_records            -> denied: datastore.record.read     # still
```

Changing one argument — `limit: 5` — makes the same sequence succeed, which is
what isolates the cause: the exact-match key is computed from `(tool_name,
args)` alone and takes no account of the permissions the new request carries.

**Why it matters:** The agent is told it was approved, so it retries the action
and is refused, so it asks again, is told it was approved, retries, is
refused — a loop that ends only when the run hits its turn limit. The person is
not asked again and sees nothing wrong; they approved it, and it still cannot
read the table. The setting that exists to stop an agent nagging instead stops
it working.

**Fix:** Record the session approvals before taking the fast path — the
exact-match check answers "may I skip the pause", which is a different question
from "what has this approval authorised". Recording is idempotent, so doing it
on both paths is safe.

**Covered by:** `test_a_session_approval_stops_repeat_asking`, marked
`xfail(strict=True)`.

---

## SURF — surfaces and notifications

### DEV-SURF-001 — Notification reads have no pod-membership gate
**Violates:** PS-SURF-030
**Severity:** medium
**Where:** `notification.list` and `notification.unread_count` on
[`notification_controller.py`](lemma-backend/app/modules/agent_surfaces/api/controllers/notification_controller.py)

**Required:** A person who does not belong to a pod is refused its
notifications, as they are refused everything else in it.

**Actual:** Both read endpoints answer `200` to a complete outsider:

```
GET /pods/{id}                          -> 403
POST /pods/{id}/notifications           -> 403  (conversation.write)
GET /pods/{id}/notifications            -> 200  {"items": [], ...}
GET /pods/{id}/notifications/unread-count -> 200  {"unread": 0}
```

**Why it matters:** No content leaks today — the query filters to the caller's
own notifications, so an outsider gets an empty list. The problem is that *is
the only thing protecting it*. There is no membership check, so the safety
depends entirely on a `WHERE` clause in a query nobody is currently thinking of
as a security boundary. The neighbouring write endpoint on the same controller
does check. That asymmetry is how a future change — a filter relaxed for an
admin view, a new `status` parameter, a join added for grouping — turns a
harmless empty list into a disclosure, with no test failing.

It is also inconsistent for its own sake: every other pod-scoped read in the
product refuses a non-member, so this one teaches the wrong lesson about what
the boundary is.

**Fix:** Put the same pod-membership dependency on both read endpoints that the
send endpoint already has. The response for an outsider becomes 403, matching
`pod.get`.

---

## PACK — bundles and apps

### DEV-PACK-001 — Any signed-in person can read any pod's app record
**Violates:** PS-PACK-031
**Severity:** medium
**Where:** [`app_controller.py:171`](lemma-backend/app/modules/apps/api/controllers/app_controller.py#L171)
(`app.get`), against [`:141`](lemma-backend/app/modules/apps/api/controllers/app_controller.py#L141)
(`app.list`)

**Required:** Someone with no access to a pod is refused its apps, as they are
refused everything else in it.

**Actual:** `app.get` returns the full app record — `id`, `pod_id`, the
creator's `user_id`, `name`, `public_slug` — to a person who is in neither the
pod nor its organization. Its immediate neighbour in the same file refuses:

```
GET /pods/{id}                    -> 403  Missing permission pod.read on pod
GET /pods/{id}/apps               -> 403  You need access to this pod to list apps
GET /pods/{id}/apps/{name}        -> 200  {"id": …, "user_id": …, "public_slug": …}
```

`app.list` declares `dependencies=[require_pod_membership("list apps")]`.
`app.get` declares no such dependency and performs no equivalent check.

Auditing the whole controller, `app.list` is the **only** operation carrying the
decorator. The write paths are nonetheless safe — `app.create`, `app.update` and
`app.delete` all return 403 to an outsider because the service checks the
permission itself. It is specifically the read that has neither guard.

**Why it matters:** Real data crosses a tenant boundary, unlike `DEV-SURF-001`
where the response was empty. An app name is often the product name of something
unreleased; `user_id` identifies a person; `public_slug` is the address the app
will be served at. It is also an existence oracle for app names in any pod whose
id is known. Any signed-in Lemma account can do it — on a shared deployment that
is every other customer.

**Fix:** Add `require_pod_membership("read an app")` to `app.get`, matching
`app.list`. While in the file, check `app.asset.root.get`, `app.asset.get`,
`app.source.archive.get` and `app.dist.archive.get` — none carry the dependency
either, and they serve content rather than metadata. (`app.source.archive.get`
answered 404 for an outsider in testing, but that was "no archive uploaded",
not a refusal.)

---

## SDK — the clients we ship

### DEV-SDK-001 — The TypeScript SDK cannot be imported from Node at all
**Violates:** *(the package is published as Node-loadable)*
**Severity:** high
**Where:** [`src/auth.ts:19`](lemma-typescript/src/auth.ts#L19) and
[`src/supertokens.ts:2`](lemma-typescript/src/supertokens.ts#L2), via
[`tsconfig.json`](lemma-typescript/tsconfig.json) `moduleResolution: "Bundler"`

**Required:** `import { Lemma } from 'lemma-sdk'` works in Node. The package
declares `"type": "module"`, `"main": "dist/index.js"` and an `exports` map with
no `browser` restriction, so it presents itself as usable server-side.

**Actual:** A clean `npm install && npm run build` produces a `dist` that Node
refuses to load:

```
Error [ERR_UNSUPPORTED_DIR_IMPORT]: Directory import
  '…/node_modules/supertokens-web-js/recipe/session' is not supported
  resolving ES modules imported from …/dist/auth.js
```

Both sources import a bare directory:

```ts
import Session from "supertokens-web-js/recipe/session";
```

`tsconfig` sets `moduleResolution: "Bundler"`, which allows that — a bundler
resolves the directory. TypeScript emits the specifier unchanged, and Node's ESM
resolver does **not** do directory imports. `supertokens-web-js` has no
`exports` map and is CommonJS, so there is nothing to redirect the subpath.

Verified end to end: `npm ci && npm run build` succeeds, `require('./dist/index.js')`
fails; rewriting the two specifiers to `…/recipe/session/index.js` makes the same
build load and export the full surface (`AgentController`, `AgentHostService`, …).

**Why it matters:** Every bundler-based consumer is fine, which is why this has
survived — Vite, webpack and Next all resolve the directory. Every **non-bundled**
consumer is broken: a Node script, a Lambda, an MCP server, any server-side
integration. Those are exactly the cases an SDK exists for, and the failure is
at import time, so nothing at all works. The package's own test suite does not
catch it because tests run through the bundler-aware toolchain rather than
against the published `dist`.

**Fix:** Append `/index.js` to both specifiers. Then add a smoke check that
loads the built `dist` in plain Node — the conformance scenario in
`tests/scenarios/journeys/clients/` does exactly that and is marked
`xfail(strict=True)`, so it turns the build red the moment this is fixed and the
marker is not removed.

---

## OPS — the platform and its own tooling

### DEV-OPS-006 — `DEBUG` defaults to on, so a 500 answers with a stack trace
**Violates:** *(no promise — the in-repo error-envelope contract in
[`exception_handlers.py`](lemma-backend/app/core/api/exception_handlers.py):
"Every error response uses one envelope: `{message, code, details}`")*
**Severity:** medium
**Where:** [`config.py:826`](lemma-backend/app/core/config.py#L826) (`debug`
defaults to `True`), consumed at [`app.py:617`](lemma-backend/app/app.py#L617)

**Required:** Every error response uses the one envelope, and an API response
never carries an exception message or a traceback. The application implements
exactly that: `handle_unexpected_exception` answers a flat
`{"message": "Internal server error", "code": "INTERNAL_ERROR"}`.

**Actual:** That handler does not run when `debug` is true. Starlette installs
an `Exception` handler as `ServerErrorMiddleware.handler`, and the middleware
checks debug **first**:

```python
if self.debug:
    response = self.debug_response(request, exc)   # full HTML traceback
elif self.handler is None:
    ...
else:
    response = await self.handler(request, exc)    # never reached
```

`debug` is `Field(default=True)`, so a deployment that does not set `DEBUG`
explicitly returns source-annotated tracebacks for every unhandled exception,
and the clean JSON handler is dead code.

**Why it matters:** A traceback names file paths, framework versions, local
variable names and the shape of internal calls — the reconnaissance an attacker
would otherwise have to guess at. It also breaks the envelope every client
parses, so a 500 is shaped differently from every other error the API returns. It is also the reason `DEV-POD-004` presents
as a stack trace to the caller rather than as an opaque 500.

The honest mitigation, so this is not read as worse than it is:
[`docs/configuration.md`](docs/configuration.md) does list `DEBUG=false` in its
production block. This is a *default* problem, not a documentation gap — the
failure mode is forgetting a line, and nothing catches it.

**Fix:** Refuse to start with `debug=true` outside local/testing, exactly as
`_require_app_base_domain_outside_local` ([`config.py:1002`](lemma-backend/app/core/config.py#L1002))
already does for a setting with no safe production default. Flipping the default
to `False` would work too, but startup validation is the stronger guarantee and
matches what this file already does elsewhere.

---

### DEV-OPS-005 — Web search with no provider reports success and finds nothing
**Violates:** PS-OPS-030
**Severity:** medium
**Where:** `agent.tool.web_search` → `POST /tools/web-search`

**Required:** Where a facility is unavailable, the system says so. A deployment
with no search provider reports the facility unavailable rather than answering
as though the search ran.

**Actual:** It answers `200` and claims it worked:

```json
{"success": true, "results": [], "message": "Web search completed successfully",
 "note": null, "error": null}
```

Found on a CI runner, which is an ordinary deployment with no search provider
configured — the same state any self-hosted install starts in.

**Why it matters:** The caller cannot tell "nothing exists" from "nothing was
looked at", and the two lead to opposite decisions. An agent handed this
concludes the web has nothing on the subject and says so to the person who
asked, with `success: true` and an explicit "completed successfully" backing it
up. A refusal would have made it re-plan or ask.

It is the shape that makes this bad rather than the emptiness. `success: false`
with a reason, or a 503, would both be fine. Reporting success is the part that
misleads.

**Fix:** Answer `503` when no provider is configured, or keep the 200 and set
`success: false` with `error` naming the missing configuration. Either way the
`message` must stop claiming the search completed.

**Covered by:** `test_web_search_says_when_it_is_unavailable`, marked
`xfail(strict=True)`.

---

### DEV-OPS-004 — No deployment built from this repository can set a spend limit
**Violates:** PS-OPS-012
**Severity:** medium
**Where:** [`usage_limit_provider.py`](lemma-backend/app/modules/usage/services/usage_limit_provider.py)

**Required:** Work that would exceed a configured limit is refused, saying which
limit was reached.

**Actual:** There is no way to configure one. Limits are resolved through a
`UsageLimitPort`, and `usage_limit_provider` is an empty extension point —
`configure_usage_limit_provider` is never called anywhere in this repository, so
`build_usage_limit_port` returns `None` and every organization is unlimited.
`usage.organization.limits.get` is read-only and there is no write counterpart.

**Why it matters:** The refusal path has therefore never run in any deployment
built from this source. That is not the same as it being broken — it is that
nobody knows, and the first time it matters is the first time somebody's bill
depends on it. It also makes `PS-OPS-010` ("limits are visible before they are
hit") a report that can only ever say "no limit".

For the open-source deployment this may be the intended posture: no billing
module, no limits. If so the promise should say that, rather than describing a
refusal nothing can produce.

**Fix:** Either ship a configuration-backed `UsageLimitPort` — the values are
already modelled in `UsageLimitValues` — or narrow `PS-OPS-012` to the
deployments that supply one, and say so in the promise.

---

### DEV-OPS-003 — Deleting a pod leaves its schedules armed and running
**Violates:** PS-OPS-020, PS-POD-050
**Severity:** high
**Where:** [`pod_service.py:157`](lemma-backend/app/modules/pod/services/pod_service.py#L157)

**Required:** Deleting a pod stops its schedules, its surfaces, and its other
standing work, and keeps them stopped.

**Actual:** `delete_pod` renames the pod, marks it deleted, and deletes its
icon. That is the whole of it — no schedule, surface, or timer is touched. After
deleting the pod:

```
GET /pods/{pod}                                   -> 404
GET /pods/{pod}/schedules                         -> 200
GET /pods/{pod}/schedules/{id}                    -> 200   {"is_active": true}
GET /pods/{pod}/schedules/{id}/runs               -> 200
```

The schedule does not merely survive as a row: it reports itself **active**, and
nothing in `app/modules/schedule/` refers to `deleted_at` or `is_deleted` at
all, so the due-schedule claimer selects on `is_active` alone and has no way to
know the pod is gone.

**Why it matters:** A deleted pod keeps waking up. Every fire starts an agent
run against a pod its operator believes no longer exists — consuming model
budget, writing to storage, and possibly messaging people through surfaces —
with no interface anywhere that will show it, because the pod is deleted. It is
the worst kind of runaway: invisible by construction, and billed.

Deletion is also what a person reaches for when a pod must stop *now* — after a
mistake, or a departure. Answering "deleted" while leaving the automation armed
makes the one emergency control in the product untrustworthy.

Surfaces do stop: the covering scenario delivers a real webhook after deletion
and nothing replies. So the teardown is not entirely absent — schedules were
missed.

**Not yet observed:** an actual post-deletion firing. The product enforces a
15-minute minimum on cron schedules, so waiting for one does not belong in a
suite that runs on every change. What is proven is that the schedule is active,
reachable, and unknown to any deletion path; the firing follows from the
claimer's query.

**Fix:** Deactivate the pod's schedules inside `delete_pod`, in the same unit of
work as `mark_deleted` — a pod that is half-deleted because a second call failed
is the state this is trying to avoid. Then make the claimer's query exclude
schedules whose pod is deleted, as the belt to that braces: the two together
mean neither a missed cleanup nor a new standing-work type can reintroduce this.

**Covered by:** `test_a_deleted_pod_runs_nothing_further`, marked
`xfail(strict=True)`.

---

### DEV-OPS-002 — `app.version` still lists a deleted entrypoint
**Violates:** *(documentation accuracy)*
**Severity:** low
**Where:** [`app/version.py:4`](lemma-backend/app/version.py#L4)

**Required:** The docstring naming the application entrypoints names the ones
that exist.

**Actual:** It still lists ``app.scheduler`` alongside ``app.app:create_app``
and ``standalone_app``. `app/scheduler.py` was deleted in `0a98cea0` (#362) with
the APScheduler removal.

**Why it matters:** Small, but it is the file people read to find out how the
application is started, and it sent this suite's harness down a dead path for an
afternoon — the CLI e2e fixture was booting the same ghost.

**Fix:** Delete the word from the docstring.
