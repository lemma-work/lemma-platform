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

## DATA — tables, records, files

### DEV-DATA-003 — Documents no converter can read retry forever, and enough of them stall the worker for every pod

**Violates:** *(no promise. The promise about unavailable converters not burning
a document's attempts is honoured — the cost of honouring it is what this is
about, and it lands on other pods.)*
**Severity:** medium
**Where:** [`file_processing_service.py`](lemma-backend/app/modules/datastore/services/file_processing_service.py),
`extraction_unavailable_claim_released` — the path that releases a claim without
counting an attempt

**Required:** A document the converter cannot reach stays queued for a later
attempt rather than being marked failed, and the unavailable dependency is not
counted against its retry budget. That is `PS-DATA-041`, it is deliberate, and
it works.

**Actual:** Because no attempt is ever counted, nothing bounds the retrying. A
document uploaded with indexing on, to a deployment whose converter is absent or
down, is re-claimed and released for as long as the file exists — and the work
is not free. Measured on one stack carrying four such files:

```
  90  datastore.file_processing_service.extraction_unavailable_claim_released.degraded
  28  Task datastore_file:01a02ccd-c125-…  failed!
  28  Task datastore_file:01a02ccd-90a5-…  failed!
  18  Task datastore_file:01a02cdd-2d96-…  failed!
  15  Task datastore_file:01a02cdd-5e59-…  failed!
   7  runtime.loop_stall.degraded
```

Four unreadable files were enough to stall the worker's event loop seven times.
The visible symptom was in a different module and four journeys away: agent runs
dispatched from Telegram surfaces stopped being answered inside ninety seconds,
in pods that had nothing to do with any of those files. Deleting the files made
the retries stop immediately and the surfaces work again — so this is the
*existence* of the documents, not a leaked task surviving them.

**Why it matters:** the condition is not exotic. It is every document uploaded
while the extraction service is down, and every document uploaded to a
deployment that never had one. The backlog is silent — the files sit at
`PENDING` with `processing_attempts: 0`, which is exactly what
`PS-DATA-041` promises and gives an operator nothing to look at — while agents
elsewhere in the deployment slow down. One tenant's unreadable PDFs become
everybody's latency, which is the same shape as the problem
`PS-DATA-042`'s backpressure exists to prevent for uploads.

**Fix:** bound the *rate*, not the attempts. A document whose converter is
unavailable should back off — exponentially, or by parking until the dependency
reports healthy again — rather than being re-claimed as fast as the queue will
allow. The attempt counter stays untouched, so `PS-DATA-041` is unaffected: what
changes is how often a hopeless claim is retried, not how many failures it is
charged with.

**Found by:** the scenario suite running against a standing tenant. The old
design gave every scenario a new pod and never accumulated enough stuck
documents to notice; four were enough.

## SURF — surfaces and notifications

### DEV-SURF-001 — A surface message goes unanswered for 240s in a full local run, and nothing says why
**Violates:** *(no promise, on the evidence — see below.)*
**Severity:** medium
**Where:** not localised. Observed through
[`test_ingestion.py`](tests/scenarios/journeys/surfaces_and_notifications/test_ingestion.py),
`test_an_unknown_sender_is_told_how_to_get_access`

The promise this scenario carries — that an answer comes back where the question
was asked — holds everywhere it has been checked: the scenario passes in
isolation and in CI, and the live lane delivers a real message to a real
Telegram account. What is recorded here is that a full local run does not get an
answer within four minutes and that the cause is not known. Calling the promise
broken would be claiming more than has been shown.

**Required:** A message delivered to a surface is answered on that surface. The
scenario signs a Telegram update, delivers it to the webhook path Lemma itself
registered, and waits for the agent's reply.

**Actual:** In a local run of the whole suite the reply never arrives. The wait
gives up after **240 seconds and 2,377 polls** having seen nothing — not a slow
answer, no answer. The same scenario passes:

- on its own (0.7s),
- with its own journey, all 38 of them,
- paired with the file journey that was starving the worker,
- and in CI, which shards by journey so no stack ever carries more than one.

**What it is not.** Each of these was tested, not reasoned about:

- *Not the worker queue.* `SCENARIOS_WORKERS=3` was added to run the replica
  shape the product is built for — `schedule_poller` says "Every replica runs
  this. Nothing elects a leader; the claim decides who fires" — and the failure
  is identical with three workers as with one.
- *Not the retrying-document storm.* That was real and is fixed separately: the
  scenario that uploads an unreadable document now deletes it, which took its
  own journey from 98s to 41s and one neighbouring scenario from 59.0s to 0.6s.
  This failure survives that fix.
- *Not the `channel_send_failed` warnings in the same log.* Those are Resend
  401s from the fast lane's placeholder key, on a different platform.
- *Not the mailbox change.* The journey passes 48/48 in isolation with real
  sub-addressed addresses configured.

**Why it matters:** it is the only red in an otherwise green suite, and the
first thing a person or an agent runs locally is the whole suite. A failure
that appears only at full size, with no error and no log line naming a cause,
is the kind that gets re-diagnosed from scratch every time somebody meets it —
which is what this file exists to stop. It may also be real: nothing here
proves the product would answer given longer, only that it did not answer in
four minutes.

**Fix:** unknown, and finding it is the work. The next step is a bisect — halve
the journey list until the smallest set that reproduces it is known — then look
at whether the agent run was dispatched at all, dispatched and never completed,
or completed with its reply never leaving. Those are three different bugs and
the evidence so far does not distinguish them.

**Found by:** running the full suite locally, repeatedly, while getting the rest
of it green. It has failed the same way on every full local run in this
sequence, before and after every change made to the suite.

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

### DEV-OPS-007 — A deleted pod keeps serving its schedules, agents and records

**Violates:** PS-OPS-020
**Severity:** high
**Where:** [`service.py:509`](lemma-backend/app/core/authorization/service.py#L509)
(`build_user_context`), reached through
[`dependencies.py:161`](lemma-backend/app/core/authorization/dependencies.py#L161)
(`resolve_pod_context`)

**Required:** Deleting a pod stops the work it was doing and stops showing it.

**Actual:** `pod.delete` is a soft delete — it sets `is_deleted`, renames the
pod, and disarms its schedules. Nothing in the authorization path notices. Pod
membership rows survive the delete, so the caller's role snapshot still
authorizes them, and every pod-scoped route that resolves the pod only through
`PodContextDep` keeps answering.

Measured against a running stack, immediately after `DELETE /pods/{id}`:

| request | answers |
|---|---|
| `GET /pods/{id}` | 404 |
| `GET /pods/{id}/members` | 404 |
| `GET /pods/{id}/schedules` | 404 |
| `GET /pods/{id}/datastore/tables` | 404 |
| `GET /pods/{id}/schedules/{schedule_id}` | **200** |
| `GET /pods/{id}/schedules/{schedule_id}/runs` | **200** |
| `GET /pods/{id}/agents` | **200** |
| `GET /pods/{id}/datastore/tables/{name}/records` | **200** |

The split is not random. The routes that refuse carry
`require_pod_membership(...)`, which resolves the pod and 404s on a deleted one
([`schedule_controller.py:82`](lemma-backend/app/modules/schedule/api/controllers/schedule_controller.py#L82)).
The routes that answer carry only `PodContextDep`
([`schedule_controller.py:173`](lemma-backend/app/modules/schedule/api/controllers/schedule_controller.py#L173)),
and that dependency never reads the pod at all on a cache hit — deliberately,
and the comment above it says why: reading the `Pod` row to build the snapshot
key "paid for it on every pod request no matter how warm the cache was."

**Why it matters:** Deleting a pod is what a person does when they want its
contents gone. Its records, its conversations and its schedule history stay
readable to everyone who had access, indefinitely, through URLs that are easy to
still be holding — a bookmark, a link in a message, an open tab. It is also
inconsistent enough to be a trap for anybody adding a pod-scoped route: whether
a deleted pod is visible depends on which dependency the route happened to use.

**Fix:** Not a one-line check in `build_user_context` — putting it there
reinstates exactly the per-request `Pod` read that was optimised away, on the
hot path of every pod-scoped request. Two shapes that do not:

- Invalidate the pod's role snapshots when it is deleted, so a deleted pod is
  always a cache miss, and refuse there — where the `Pod` row is already being
  read ([`service.py:541`](lemma-backend/app/core/authorization/service.py#L541)).
- Or carry `is_deleted` in the cached snapshot itself, so the check costs
  nothing and the value is invalidated by the same path that writes it.

Either way the check belongs in one place, so a new pod-scoped route inherits it
rather than having to remember it.

**Found by:** `test_a_deleted_pod_runs_nothing_further`, which has been failing
in the scenario suite. It is `xfail(strict=True)` until this is fixed.

## POD — pods, membership and roles

### DEV-POD-005 — Is an organization owner exempt from the last-admin rule? The code and the commit that shipped it disagree
**Violates:** *(question — the divergence may be deliberate, and the promise may
be the thing that is wrong. Resolve it with a product decision before writing
code. The promise in question is `PS-POD-041`.)*
**Severity:** question
**Where:** [`pod_member_service.py:286`](lemma-backend/app/modules/pod/services/pod_member_service.py#L286)

**The disagreement.** `_refuse_if_last_administrator` returns early for an
organization owner, and its docstring argues the case at length:

> "Organization owners are exempt, and that is the difference between this and
> the organization-level guard beside it. Zero organization owners is permanent
> -- no path mints one. A pod without a POD_ADMIN is not stuck at all: its
> organization's owners reach every pod in the organization [...] Refusing an
> owner here would hand a sole operator advice they cannot follow."

The commit that shipped the rule (#452) says the opposite in its own message:

> "DEV-POD-002 (PS-POD-041): removing the last pod admin, or demoting them, is
> refused — the rule is about the pod, so it applies **even when the requester
> is an org owner** whose permission bypass would otherwise allow it."

`PS-POD-041` itself says "at all times", with no exemption written down.

**Why it matters:** an organization owner can today demote themselves to
`POD_VIEWER` in a pod where they are the only admin, and it succeeds — verified
against a running stack. Whether that is correct depends entirely on which of
the two intents above is the real one, and nothing in the repository settles it.
Until it is settled, no scenario should pin the behaviour: a test asserting the
exemption would bless a possible bug, and one asserting the refusal would fail
against shipped code that may well be right.

**Fix:** a product decision, then one of two small changes. If owners are
exempt, say so in `PS-POD-041` and add a scenario. If they are not, delete the
early return and its docstring paragraph — the rest of the rule already works.

**Found by:** rewriting `test_the_last_pod_admin_cannot_step_down`, which had
been demoting an organization owner and so was exercising this exemption while
reporting itself as proof of the rule.
