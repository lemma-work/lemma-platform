# Coverage and reporting

Use these compact structures to plan a Lemma app QA pass and make its claims
auditable. Tailor them to the requested scope; do not create empty ceremony.

## Journey ledger

Create one row per user outcome, ordered by release risk.

| ID | Priority | Persona / authority | Start state | Journey | Expected UI | Durable assertion | Forbidden effect | Environments / viewports | Result |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| J-01 | Gate | Pod user; owns fixture | Empty assigned queue | Create request → submit → reopen | Confirmation, row appears, detail opens | One owned row with exact fields | No duplicate; no other owner | local + deployed; wide + 375 | Pending |

Use `Gate` for journeys that decide the release. Mark results `Pass`, `Fail`,
`Blocked`, or `Not run`; never use a blank cell to imply success.

## Coverage ledger

Track the dimensions that a happy-path journey can otherwise hide.

| Dimension | Required evidence | Result / limitation |
| --- | --- | --- |
| Build/static checks | Exact command and exit result | |
| Local primary journey | Browser checkpoints + durable assertion | |
| Deployed primary journey | Release marker + browser checkpoints + durable assertion | |
| Pod-shell embedding | Real shell route; iframe sizing, focus, navigation, auth, reload | |
| Auth/session | Actor, role, app boot result, relevant response codes | |
| RLS/multi-user | Two real principals and positive + negative pod assertions | |
| Delegated workload | Invoker, workload grants, run id, output/effect | |
| Persistence | Reload/deep-link plus exact durable object | |
| Loading/empty/error/permission | Visual and interaction checkpoint for each applicable state | |
| Responsive | Wide, 375px, and any layout breakpoint exercised | |
| Accessibility | Keyboard path and inspected semantics; list AT not exercised | |
| Console/network | Errors, failed requests, duplicate calls, CORS/auth findings | |
| Cleanup | Exact ids/paths removed or listed as residual | |

## Evidence bundle

Prefer a small evidence bundle that can independently support the claim:

- `context.md` or report header: time, app, pod, server, URL, revision/release,
  browser session, actor, role, and allowed mutations;
- screenshots: `J-01-01-start.png`, `J-01-02-action.png`,
  `J-01-03-result.png`;
- browser excerpts: errors, relevant console lines, and filtered request summary;
- pod assertions: exact CLI command and redacted output for the affected
  record/file/run/conversation;
- `J-01.har` or a trace only when ordering, timing, or intermittent requests are
  material.

View every screenshot before citing it. Redact secrets and unrelated private data.
Do not dump full network bodies when status, endpoint, method, timing, request id,
and a small redacted response excerpt prove the issue.

## Durable assertion map

Select only the relevant Lemma operator command after reading the `lemma-user`
skill and current CLI help.

| Claimed effect | Independent assertion |
| --- | --- |
| Record created/updated | `lemma records get <table> <record-id>`; compare ownership and exact fields |
| Record absent/isolated | scoped `lemma records list <table>` and exact get as the tested principal |
| File uploaded/processed | `lemma files stat <path>`; inspect indexing status if search is claimed |
| Workflow advanced | `lemma workflows runs get <run-id>`; inspect status, active wait, history, error/output |
| Function completed | inspect the exact function run id, status, error/logs, and output data |
| Agent answered/acted | inspect the exact conversation messages and independently verify every claimed mutation |
| Connector write occurred | inspect the exact controlled destination plus Lemma operation/run result |
| Deploy is current | app detail/release id plus cache-busted live revision marker |

Capture object ids from the UI or request as the action occurs. Avoid broad queries
whose result could be satisfied by pre-existing data.

## Permission evidence matrix

Prove both what the actor may do and what they must not see or mutate.

| Case | UI expectation | Server/pod expectation |
| --- | --- | --- |
| Same owner, valid role | Action available and succeeds | Exact object readable/writable |
| Different owner on RLS table | Protected row omitted; no leaked count/detail | List omits it; exact get commonly returns `404` |
| Insufficient human role | Action absent or explains denial | Exact `INSUFFICIENT_PERMISSION` or current equivalent |
| Missing workload grant | Agent/function action explains failure | `MISSING_WORKLOAD_RESOURCE_GRANT` names workload/resource |
| Personal resource mismatch | No path/content leak | `PERSONAL_RESOURCE_DENIED`; no grant override |
| Delegation wiring error | Safe actionable failure | `DELEGATION_SCOPE_VIOLATION`; do not add a grant blindly |

Treat current server error codes and CLI/API output as authoritative. Update the
report wording if implementation terminology differs.

## Defect record

Use one self-contained block per confirmed issue:

```markdown
### QA-001 — Short outcome-based title

- Severity: Critical | High | Medium | Low
- Evidence status: Confirmed | Intermittent | Suspected
- Journey: J-01
- Environment: server, pod, app URL, revision/release
- Actor: user/agent identity and role; delegated workload if any
- Evidence: screenshot paths, request id/HAR, redacted pod assertion

Expected: Describe the requirement and intended durable effect.
Actual: Describe the observed UI and durable state precisely.

Reproduction:
1. Restore the named fixture/start state.
2. Perform one observable action.
3. Observe the broken UI or response.
4. Query the exact pod object and observe the mismatch.

Impact: Name the affected persona, task, data, and workaround.
Notes: Record frequency, suspected boundary, and what was ruled out without
presenting an unproven root cause as fact.
```

Use `Confirmed` after a clean repeat from a known state. Use `Intermittent` when a
failure repeats inconsistently but has traceable evidence. Use `Suspected` for a
signal that needs more access or instrumentation; keep it outside confirmed issue
totals.

## Final report

Lead with the release decision, not the testing diary:

```markdown
# <App> QA — <date>

Verdict: PASS | PASS WITH RISKS | FAIL | NOT VERIFIED
Scope: <app, pod, server, revision/release, identities, environments>

## Decision summary
<What is safe or unsafe to do, for whom, and why.>

## Release gates
| Journey | Local | Deployed | Durable state | Result |
| --- | --- | --- | --- | --- |

## Findings
<Severity counts, then defect records in severity order.>

## Coverage and boundaries
<Coverage ledger, untested areas, mocks, tool/environment failures.>

## Evidence and cleanup
<Artifact paths, fixture ids, items removed, residual effects.>
```

Do not bury `NOT VERIFIED` behind build success or partial screenshots. State the
single next action that would convert each material coverage gap into evidence.
