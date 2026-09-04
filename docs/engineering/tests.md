# Test design

[docs/testing.md](../testing.md) says **which** suite a change needs and what
gates what. This document says what a good test in any of them looks like.

Ids are `TST-NN`. Each rule names its check and today's measurement; a non-zero
count is a **ratchet**, zero is **hard** — and two rules below are at zero today
and worth keeping there.

---

## The one idea

A test earns its place by being able to fail for the reason it claims.

Most weak tests here are not wrong, they are *inert*: they pass whether or not
the behaviour they name works. Three ways that happens, all measured below — the
test asserts nothing; the test asserts that a mock was called; or the test
patches the very thing it is exercising, so the real code never runs.

The estate is large and, in the places that matter most, good: 6,491 backend
tests, a 405-test black-box scenario suite traced to a numbered specification,
and meta-gates that each exist because they caught a real regression. The rules
below are about the gap between that and the doctrine already written down.

| Suite | Tests |
|---|---:|
| backend unit + module e2e | 6,491 |
| product scenarios | 405 |
| `lemma-cli` | 584 |
| `lemma-python` | 68 |
| `lemma-typescript` | 254 |

---

## Rules

### TST-01 — every test asserts something a reader can see

`assert`, `pytest.raises`, or a named `assert_*` helper. "It did not raise" is
written `with does_not_raise():`, never as a body with no assertion.

*Check:* AST pass in the census gate, ratcheted
*Today:* **79** backend, **50** scenario, 2 CLI, 1 SDK assertion-free tests —
ratchet

### TST-02 — a test may not patch a symbol that resolves inside the unit under test

If `test_exporter.py` patches `exporter._build_manifest`, the exporter no longer
runs. Inject the collaborator through the constructor or factory seam the code
already has — and if there isn't one, that is the finding.

*Check:* `make lint-test-doubles` — an AST pass over every `test_*.py` and
`conftest.py` for a double installed on a name inside a module the file
imports, in all three forms the codebase uses (`patch("a.b.c")`,
`patch.object(module, "name")`, and `monkeypatch.setattr`, which is most of
them). The subject is read from the imports because the filename cannot supply
it: 571 test files have a stem no source file answers to, which put two thirds
of the repo's patch calls beyond the old rule's reach —
`test_schedule_idempotency_regression.py` scored zero while installing twenty
doubles on the service module it drives, and no `conftest.py` was read at all.
Swapping a name for a *value* (`settings.api_url`) arranges the run rather than
doubling a unit, and does not count.
*Today:* **1,345** — 1,170 in the backend and 175 in the CLI, recorded per
module in `lemma-backend/test-doubles-baseline.json` — ratchet, the number only
goes down. It is four times the 326 previously published because the old survey
could only see a test whose name matched a source file, not because anything
grew.

### TST-03 — a fake implements the port; it does not patch attributes on a real object

Fakes live in `app/modules/test_support/fakes.py` or a module's `tests/fakes.py`,
and are type-checked against the `Protocol` they stand for. That is what makes a
fake fail when the port changes — which is the entire point of having one.

`FakeUnitOfWork`, `InMemoryRepository`, `PassthroughEventInbox` and
`ValidationTerminalEventInbox` (119 lines, all of `test_support/fakes.py`) do
this correctly. Copy them.

*Check:* `basedpyright` over `test_support/` and each `fakes.py`, in
`typecheck-critical`

### TST-04 — assert on resulting state, not on the fact that a collaborator was called

`assert_called_with` is legitimate where the call *is* the contract — an outbound
webhook, an idempotency key, a payment. Everywhere else it asserts that the code
is written the way it is written, and it passes after the behaviour breaks.

*Check:* per-module ratchet on `assert_called*` / `assert_awaited*`
*Today:* `agent_surfaces` **152**, `datastore` **107**, `connectors` **81**,
`agent` 63, `schedule` 43, `pod` 37, `identity` 36, `function` 33, `workflow` 29,
`apps` 13, `pod_bundle` 10, `core` 4, `usage` 1, `workspace` 0, `icon` 0

### TST-05 — every endpoint family has at least one e2e test that forces a dependency to fail

[docs/testing.md](../testing.md) calls this the module e2e suite's "most valuable
work", on the grounds that scenarios cannot reach it — the scenario suite forbids
mocking, so there is no way to make a real dependency fail on demand.

A family is one `tests/e2e/` directory. Provider 5xx, storage 503, Redis
unavailable, or a write racing a write — whichever that family actually depends
on.

*Check:* census mapping each `tests/e2e/` directory to a failure-injection count,
failing when any directory is at zero
*Today:* **34** of 152 files inject a failure; **5** of 14 directories inject
none (`pod` 0/16, `pod_bundle` 0/11, `identity` 0/5, `usage` 0/2, `apps` 0/2).
Best today: `connectors` 8/16, `agent` 7/29 — ratchet

### TST-06 — a refusal test asserts the exact status code

`>= 400` is permitted only where the subject genuinely is "not allowed, however
expressed", and then through a step that says so. Otherwise a 500 satisfies a
test written for a 403 — which is how an existence leak passes a permission test.

*Check:* `journeys/test_harness_contract.py` rejects a bare `status_code >= 400`
in a journey body
*Today:* **9** loose comparisons in the backend, **2** in the scenario journeys —
ratchet

### TST-07 — wait on a condition, never on the clock

Use `app/modules/test_support/e2e/waiters.py` (`eventually`, `wait_for_status`).
A sleep is either too short and flakes under load, or too long and everyone pays
for it on every run.

*Check:* widen `scripts/check_e2e_wait_patterns.py` from loop-shaped polls to any
`sleep` in a `test_*.py` outside the canonical waiters
*Today:* the loop rule is enforced (9 baselined); **89** non-`sleep(0)` sites
remain (`app/core` 22, `workspace` 15, `agent` 14) — ratchet

### TST-08 — a test name states the behaviour

`test_<subject>_<does what>_<under what condition>`. No `test_basic`,
`test_works`, `test_create`, `test_flow`.

*Today:* **0** violations across 7,548 test functions — **hard**, hold the line

### TST-09 — a fixture is under 200 lines; a test module is under 1,000

*Today:* **0** fixtures over 200 lines — hard. **30** test modules over 1,000
lines, largest 3,388 — ratchet

### TST-10 — every test lives in `tests/unit/` or `tests/e2e/`

A module that invents a third location gets a lane it did not choose: `schedule`
has no `tests/unit/` at all, so 167 unit-shaped tests run in the slower lane and
the module carries the repo's lowest coverage floor as a result.

*Check:* collection-time check in the root `conftest.py`
*Today:* **1** violating module (`schedule`, 27 files); `workspace` uses
`integration/` (11 files) — permitted, but gated by TST-11

### TST-11 — a test that can only skip on a runner is deselected by that lane, not skipped in it

A test that skips at runtime still looks like coverage in a listing. Mark it and
let the lane's `-m` expression deselect it.

*Check:* `check_pytest_census.py` `ENVIRONMENT_GATED`
*Today:* `integration` carries 124 tests and is selected by `UNIT_MARKERS` while
gating on Docker/E2B/network at runtime — ratchet

### TST-12 — every registered marker is carried by a test, and every lane selects a non-zero count

A lane that selects nothing is green forever.

*Today:* `empty_markers` = `identity`, `local_cli`, `pod`, `protected` — and the
protected-e2e workflow selects `local_cli` and `protected`, both empty — hard
once cleared

### TST-13 — coverage floors are per module and ratcheted; the global number is advisory

One global percentage lets a small well-tested module pay for a large untested
one. `check_coverage_thresholds.py` already accepts repeated `--min-module`.

*Today:* `--min-total 70 --min-module schedule=65`. Unfloored, by production
lines: `agent` (47,316), `agent_surfaces` (41,847), `core` (27,481), `datastore`
(22,268), `connectors` (16,374), `workspace` (12,593), `pod_bundle` (10,050),
`identity` (8,597), `workflow` (7,511), `function` (6,563), `pod` (5,108),
`apps` (3,820), `usage` (2,789), `icon` (500)

### TST-14 — every bug fix ships a test that fails before it and passes after

Write it first and watch it fail. A test written after the fix, against the fixed
code, proves that the code does what it does.

### TST-15 — the CLI and the SDKs are tested against a contract, not a mock of themselves

CLI tests patch `run_with_client` at 88 sites, so no CLI test exercises the
mapping from arguments to an SDK call — the layer where every one of this
release's CLI defects lives. Drive the CLI through `CliRunner` against a fake
HTTP transport instead.

Related: **329** CLI assertions read rich-rendered stdout. Assert on the `--json`
payload; the rendered text is a presentation detail that changes with a theme.

*Today:* CLI **88** `run_with_client` patches; `lemma-python` has 68 tests for
4,730 hand-written lines and no test of auth or token refresh — ratchet

### TST-16 — the deterministic model is part of the contract it stands for

The scenario suite's model reports no token usage, never collides a tool-call id,
and never emits partial JSON tool arguments — so the accounting, dedup and
parsing paths that depend on those are unexercised by every test that uses it.
When you add a behaviour that depends on the model's shape, extend the double.

---

## The scenario suite

The four rules enforced by `journeys/test_harness_contract.py` — nothing imports
the backend, nothing is mocked, nothing sleeps, every test says what it proves —
are stated with their reasoning in
[tests/scenarios/CONVENTIONS.md](../../tests/scenarios/CONVENTIONS.md). Read that
before adding one; it is the best-argued testing document here, and its
"how a scenario passes while proving nothing" section is a list of real defects
from its own history.

Two things it says that are worth repeating because they are the ones people
argue with:

- **A divergence is a finding, not an edit.** If the system does not behave the
  way a scenario says, mark it `gap` and fix the code. A specification that
  cannot fail is documentation.
- **Create everything you assert on.** The tenant is shared across every run that
  has ever touched it, so never assert on a total.

## Related

- [docs/testing.md](../testing.md) — which suite, which lane, what gates a merge
- [design.md](design.md) — the seams that make a unit testable in the first place
- [types.md](types.md) — TST-03 depends on the port actually being typed
