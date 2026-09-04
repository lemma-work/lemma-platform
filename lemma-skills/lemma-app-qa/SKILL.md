---
name: lemma-app-qa
description: "Test and verify Lemma pod apps through real end-to-end user journeys in authenticated local and deployed contexts. Use when asked to QA, dogfood, smoke-test, regression-test, acceptance-test, reproduce, or verify a Lemma app; validate UI behavior against durable pod state; check auth, RLS, delegated workloads, permissions, loading/empty/error states, responsive layout, accessibility, console/network health, or deployment correctness; and produce evidence-backed defects and a calibrated release verdict. Use the browser skill for browser command mechanics."
---

# Lemma App QA

Prove a precise contract: **this principal can complete this journey in this app,
and the expected effect persists in this pod under the correct authority**. Test
outcomes, not pages. Treat a rendered screen, a successful build, `READY`, or an
HTTP `200` as evidence—not completion.

## Load the right companions

- Read the `browser` skill before driving the app. Let it own browser startup,
  snapshots, refs, waits, screenshots, console, network, sessions, and recovery.
  Do not invent or duplicate its command catalog here.
- Read the `lemma-user` skill before verifying records, files, runs, messages, or
  other pod state from the CLI.
- Read `lemma-builder/references/apps.md` when the app's build, runtime context,
  SDK, deployment, or iframe behavior is in question.
- Read `lemma-builder/references/authorization-model.md` before judging RLS,
  human roles, workload grants, delegation, personal resources, connectors, or
  destructive approvals.
- Read [coverage-and-reporting.md](references/coverage-and-reporting.md) before a
  multi-journey pass and before issuing the final report.

## Set the verification boundary

Record these facts before testing:

- target app slug or local URL; pod id/slug and server/environment;
- local revision and deployed release id/timestamp when available;
- actor identity and pod role; delegated agent/function/workflow when involved;
- allowed mutations, external side effects, cleanup policy, and out-of-scope areas;
- acceptance source: user request, issue, `DESIGN.md`, product spec, or observed
  contract;
- required environments and viewports.

Use a disposable fixture prefix and retain every created record/file/run id. Do
not modify or delete real user data merely to gain coverage. Do not trigger email,
payments, connector writes, destructive grants, or other external effects without
explicit authority and a controlled account/fixture.

Keep claims inside the observed boundary. Do not present a local pass as a
deployed pass, one identity as multi-user coverage, an accessibility-tree scan as
screen-reader verification, a mocked denial as proof of server authorization, or
a browser-tool failure as an app defect.

## Build a journey ledger

Translate requirements into a short, risk-ordered ledger. Define each journey by:

1. persona and authority;
2. starting state and fixtures;
3. actions and decision points;
4. expected visible states;
5. expected durable pod effects;
6. forbidden effects or data visibility;
7. required environment and viewport.

Always include the primary value journey. Add only relevant boundary journeys:
retry/recovery, invalid input, refresh/deep-link persistence, concurrent or live
updates, permission denial, second-user isolation, assigned workflow waits,
delegated agent actions, file/connector behavior, and deployment freshness.

Prefer five deep journeys with durable verification over fifty shallow clicks.
Use the ledger in [coverage-and-reporting.md](references/coverage-and-reporting.md).

## Establish a baseline

1. Confirm the exact pod and app with Lemma CLI inventory/detail commands.
2. Inspect relevant table schemas, existing fixture rows, file paths, workflow or
   function definitions, and workload grants without widening access.
3. Capture the pre-test state needed to prove later mutations.
4. Run the repository's focused static/build checks when source is in scope.
5. Note pre-existing browser errors, failed requests, and unavailable dependencies.

Separate source checks from product checks in the report. A build pass cannot
substitute for a browser journey; an already-broken environment cannot be charged
to the new revision without a causal repro.

## Open the correct browser context

Use the Lemma CLI to establish auth instead of copying or hand-wiring tokens:

```bash
# Deployed app: resolve its served URL and inject current Lemma auth.
lemma apps open <app-slug> --pod <pod>

# Local `npm run dev`: use only when that dev server seeds its own dev token.
lemma apps open --url http://localhost:<port> --no-auth
```

Use `--no-auth` only for the self-authenticating local dev flow—not to simulate a
signed-out user. Test sign-out, access request, or another role in an isolated
browser context with a real corresponding identity. Browser profiles and sessions
do not inherit auth from one another.

Exercise local development first when diagnosing quickly, then repeat every
release-critical journey against the deployed app. Confirm that the deploy landed:
record the release detail, hard-refresh or cache-bust, and verify a revision-unique
UI/DOM marker before attributing results to the new build.

Treat `lemma apps open` as proof of the served app, not automatically of its host.
When people normally enter through the Lemma pod shell, repeat a critical smoke
through that real shell route and verify iframe sizing, focus, navigation, auth, and
reload behavior. Add desktop or other hosts only when they are part of the release
contract.

## Execute each journey

For every ledger row:

1. Restore its declared starting state.
2. Capture the initial URL, interactive snapshot, and visual checkpoint.
3. Act through semantic browser locators and waits; re-snapshot after every page
   change or meaningful re-render.
4. Assert feedback at each decision point, not only the final page.
5. Capture the final visible state and inspect browser errors, console output, and
   relevant network requests. Start a HAR/trace only for timing or intermittent
   failures.
6. Reload or reopen the deep link when persistence is part of the contract.
7. Verify the durable effect independently through Lemma CLI/API state.
8. Restore or record the resulting fixture state before the next journey.

For a suspected defect, reproduce once from a known state before filing it.
Capture the smallest complete sequence: before, action, broken result, console or
request evidence, and pod-state evidence. Mark a one-off signal as intermittent or
unconfirmed; do not inflate it into a deterministic defect.

## Verify the pod, not only the toast

Match every meaningful UI mutation to an independent durable assertion:

- record create/update/delete → fetch the exact record id or scoped list;
- file upload/edit/delete → inspect the exact path and processing status;
- workflow action → inspect run status, active wait, step history, and output;
- function action → inspect the named run, status, logs/error, and output data;
- agent action → inspect the conversation's final output and any claimed effects;
- connector action → inspect the Lemma run/result and the controlled destination;
- live update → prove the second view changes without manual reload or polling.

Assert both presence and absence. A successful create must produce one correctly
owned row, not duplicates; an unauthorized user must not see it; a failed submit
must not leave a partial record. Never infer backend success from optimistic UI.

## Test identity, RLS, and delegation deliberately

Use real authorized principals when access behavior is acceptance-critical. Keep
each identity in its own browser session and record which one produced each
artifact.

- On an RLS table, expect a list to omit another user's row and an exact fetch to
  return `404`; do not label that as data loss without checking ownership.
- Avoid admin-mode reads during ordinary app verification. They bypass the user's
  product view and do not prove the app contract.
- Distinguish `401` auth failures from human-role denial, workload-grant denial,
  delegation-scope violations, and personal-resource denial. Report the exact
  response/error code.
- For an agent/function/workflow action, verify the invoking user, the workload's
  explicit grants, and the resulting RLS-scoped effect. Do not broaden grants to
  make a test pass unless the user asked for a fix.
- Verify negative cases server-side. Hiding a button is useful UX, but it is not
  authorization enforcement.

## Cover product states and surfaces

Exercise these dimensions where the feature exposes them:

- **Loading:** show stable structure, prevent duplicate actions, and transition
  without flicker or stale content.
- **Empty:** explain why it is empty and offer the correct next action; distinguish
  true emptiness from RLS filtering or failed loading.
- **Error/retry:** preserve user input where safe, expose an actionable message,
  and recover without duplicate writes.
- **Permission:** explain unavailable access without leaking protected data; keep
  the denial consistent with server behavior.
- **Full/overflow:** use long labels, many rows, large values, and pagination or
  scrolling without clipping essential actions. Seed past one page — `record.list`
  serves at most 1000 rows and an ad-hoc datastore query answers with `truncated`
  — then check every displayed count against an independent count. A page size
  rendered as a total is a data defect, not a layout one.
- **Responsive:** check at minimum the product's wide layout and `375px`; add
  intermediate widths for layout transitions. Reject hidden essential actions,
  accidental horizontal scrolling, clipped dialogs, and touch targets below the
  app baseline of roughly `44px`.
- **Accessibility:** complete the primary journey by keyboard; inspect focus order,
  visible focus, accessible names, labels, headings, dialog focus/restore, status
  announcements, and non-color status cues. State explicitly what was not tested
  with assistive technology.
- **Browser health:** investigate uncaught errors, failed requests, CORS/auth
  failures, duplicate mutation requests, refetch loops, and unexpected polling.

Use safe request stubs/aborts to inspect recoverable UI error states. Do not use a
mocked response to claim that RLS, roles, grants, or persistence work in the backend.

## Triage and report

Classify severity by user and data impact, not visual drama:

- **Critical:** expose another user's/private data, bypass authority, corrupt or
  lose data, cause an uncontrolled irreversible side effect, make the target app
  unavailable, or crash/block its only core journey for all target users.
- **High:** block a core journey for a target segment with no reasonable
  workaround, persist an incorrect mutation, or fail only after deployment.
- **Medium:** materially degrade a journey while a workaround exists, or break an
  important state, viewport, or accessibility path for a subset of users.
- **Low:** create a bounded content, visual, or polish defect without task or data
  risk.

Keep severity separate from evidence status and release scope. Use the evidence and
defect templates in [coverage-and-reporting.md](references/coverage-and-reporting.md).
Report exact URLs, identity/role, pod/environment, fixture ids, timestamps,
expected versus actual behavior, minimal repro steps, and artifact paths.

Issue one verdict:

- **PASS:** pass every required critical journey in every required environment;
  leave no critical/high defect or unverified release gate.
- **PASS WITH RISKS:** pass core journeys but retain bounded defects or explicitly
  accepted coverage gaps that do not invalidate the release.
- **FAIL:** break a critical journey, authorization boundary, durable-state
  contract, or release gate.
- **NOT VERIFIED:** lack the auth, identity, environment, browser, dependency, or
  observability needed to make the requested claim.

List untested areas and tooling failures beside the verdict. Never silently convert
`NOT VERIFIED` into `PASS`.

## Finish cleanly

Re-read totals and ensure every issue maps to a ledger row or exploratory finding.
Remove only disposable fixtures created by this test, by exact recorded id/path,
when cleanup is authorized. Verify their removal. List any residual records, files,
runs, or external effects that could not be reversed.

Return an answer-first summary, the verdict, critical journey results, defects by
severity, verification ledger, coverage gaps, and evidence locations. Preserve the
raw screenshots/HAR/traces only when they add diagnostic value; never include
tokens, cookies, credentials, private row contents, or connector secrets.
