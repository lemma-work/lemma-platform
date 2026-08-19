# Testing strategy

Lemma has three kinds of test, and they answer three different questions. Most
arguments about testing here are really arguments about which question is being
asked, so start there.

| Suite | The question it answers | Where |
|---|---|---|
| **Unit** | Is this unit correct? | `lemma-backend/app/**/tests/unit/` |
| **Module e2e** | Does this module's HTTP surface behave — including when the things it depends on fail? | `lemma-backend/app/modules/*/tests/e2e/` |
| **Product scenarios** | Does Lemma keep its promises to a person, over a real socket? | `tests/scenarios/` |

A test that answers none of the three is the only kind that should simply be
deleted.

---

## Which one am I writing?

**Is it a promise the product makes to somebody using it?** Then it is a
scenario. It belongs in `tests/scenarios/`, it names a `PS-` id from
[the product specification](product/README.md), and the specification moves to
`covered` in the same pull request — after you have watched it pass.

**Is it about what happens when something fails?** A provider times out, storage
returns 503, a write races another write. Then it is a module e2e test.
Scenarios cannot reach those paths: the suite forbids mocking, and there is no
way to make a real dependency fail on demand. This is the module suites' most
valuable work and it is not replaceable.

**Is it about one function, one class, one rule?** Then it is a unit test. It
should not need Postgres.

The common mistake is writing a scenario for something that is not a promise —
a helper's edge case, a serializer's shape. Those pass, they cost minutes of
stack time, and they say nothing anybody outside the team would recognise.

---

## Why the scenario suite is shaped the way it is

Two properties are worth understanding before adding to it, because they explain
rules that otherwise look like dogma.

**It goes over a real socket, and the module e2e suite does not.** Module e2e
tests use `httpx.ASGITransport`, which hands a request straight to the
application object. That is fast, and it means those tests **never run the
application's lifespan** — the fixture's own docstring says so. Everything in
`app.py::lifespan` and every module's `api_lifespans` — the thread pool, the
connection-scope monitor, analytics, SuperTokens, the job queue, the message bus
— is dark to them. A green module-e2e run does not imply a bootable system.
Scenarios boot one.

The same goes for the wire itself: body-size limits that deliberately do not
trust `Content-Length`, header limits, chunked encoding, a real WebSocket
handshake. None of it is exercised in-process.

**It is traced to a specification that can fail.** `docs/product/` states what
the product is *meant* to do, not what the code does. When the two disagree the
default assumption is that the code is wrong. That is what makes a `gap` status
meaningful, and it is why the rule in
[CONTRIBUTING.md](../CONTRIBUTING.md) exists: if the system does not behave the
way a scenario says, do not edit the scenario.

---

## The lanes, and what runs when

| Lane | Command | Runs |
|---|---|---|
| Backend unit | `make test-backend-unit` | Every push. **The only required check.** |
| Backend e2e | `make test-e2e-fast` | After CI succeeds, via `e2e.yml` |
| Scenario gates | `make scenarios-guards`, `make scenario-coverage` | Every pull request |
| Scenarios (fast) | `make scenarios` | Nightly, on request, or with the `run-scenarios` label |
| Scenarios (sandbox) | `make scenarios-sandbox` | Same, after building the workspace images |
| Scenarios (live) | `make scenarios-live` | Nightly and before a release. See [LIVE.md](../tests/scenarios/LIVE.md) |
| Protected e2e | — | Weekly, via `backend-protected-e2e.yml` |

The two scenario **gate** jobs are cheap and stay on every pull request, and
they carry more weight than their runtime suggests: CI's quality job runs the
individual gate targets rather than `make quality`, so the specification-honesty
checks run nowhere else. Without them a promise could be marked `covered` with
no test and nothing would notice.

The lanes that boot a stack do not run on pull requests. They take minutes, they
are about the product rather than about the change, and a ten-minute wait on
every push buys little.

### What gates a merge

Only `lemma-backend unit` is required. Everything else reports. That is a
deliberate choice about speed, and it puts the weight on reviewers rather than
on the machine — so a red `e2e.yml` or a red nightly is a thing to go and read,
not a thing to route around.

Coverage floors live in `e2e.yml` and are enforced by
`lemma-backend/scripts/check_coverage_thresholds.py`. `CONTRIBUTING.md` names
coverage below floor as a merge blocker. Run the command rather than quoting a
number:

```bash
make coverage-backend
```

---

## The two suites overlap, and both stay

The module e2e suite and the scenario suite touch many of the same endpoints.
That overlap is not duplication to be cleaned up. They are asking different
questions, so the same endpoint answering both is the normal case.

**Module e2e tests validate a module's contract.** They are meant to be fast,
they run in-process, and they can do the things a black-box test provably
cannot: force a dependency to fail, assert a query budget, read a database
post-condition, pin an exact refusal code. That is a category of coverage the
scenario suite cannot reach by design, because it forbids mocking.

**Scenarios validate a promise to a person.** They run over a socket against a
booted stack, so they see what a client sees — including the lifespan, the wire,
and the ordering of a write against the response that reports it.

Neither replaces the other, and **no test is deleted for being covered
elsewhere.** A test is deleted when it asserts nothing, or when the behaviour it
asserts is gone. Being redundant with a test in the other suite is not a reason.

This was measured before it was decided. A survey of 759 module e2e tests found
six files whose every operation a scenario already covers — the strongest
candidates for redundancy that exist. Examined by hand, **all six were still
worth keeping**, and for one consistent reason: they assert more *specifically*
than the scenarios do.

- Two assert query budgets — how many statements a request costs. No black-box
  test can see that.
- One pins the bundle format field by field. The scenario covering the same
  operations proves the exporter and importer agree with each other, which is a
  weaker claim: rename a key on both sides and it still passes.
- One pins exact refusal codes for a visibility matrix, including cases where a
  `404` becoming a `403` would leak that a resource exists. The scenarios use a
  helper that accepts any `4xx`.
- Two assert intermediate states and destructive effects — a cancelling import,
  a column actually removed — that no scenario asserts at all.

The useful direction is the other one: where a scenario is the weaker test,
**strengthen the scenario** — assert the refusal code where the code is the
point, pin the artifact format where it is an external contract. That adds
coverage instead of moving it.

### Where a new test goes

Use the three questions at the top of this document, not the overlap. A promise
to a person gets a scenario. A forced failure, a query budget, or an internal
invariant gets a module e2e test. One function gets a unit test. When a change
deserves both, write both.

---

## Rules that apply everywhere

**Test fixtures must not look like credentials.** A high-entropy literal shaped
like a real token is indistinguishable from one — to a reader, and to the secret
scanner, which is right to flag it. Build the value instead of writing it down.
`.gitleaks.toml` allows exactly one such vector and says why every other one
stays a finding.

**A test must not depend on the machine it runs on.** Not on a developer's
`.env`, not on which Docker images happen to be built, not on wall-clock speed.
Each of those has produced a suite that passed on one laptop and failed
everywhere else, and each is now guarded — see
`tests/scenarios/journeys/test_harness_contract.py`.

**Wait on a condition, never on the clock.** A sleep is either too short and
flakes under load, or too long and everyone pays for it on every run.

**A failing test is evidence, not an obstacle.** If a scenario fails because the
product is wrong, the finding goes in [`issues.md`](../issues.md) with a `DEV-`
id, the promise moves to `gap`, and the scenario is marked
`xfail(strict=True)` so the build turns red the moment somebody fixes it.

---

## Known limits

Written down because a limit nobody has recorded gets rediscovered as a bug.

- **Scenario coverage feeds no gate.** The suite can measure backend coverage
  (`SCENARIOS_COVERAGE=1`), but `scenarios.yml` does not upload it, so the
  floors in `e2e.yml` see module e2e coverage only. So the floors under-report
  what is actually exercised, and a line only a scenario reaches counts as
  uncovered.
- **Consent flows cannot be automated.** Google deliberately blocks automated
  sign-in. The live lane proves the connect flow a deployment hands a person —
  the right client, the scopes, offline access — and stops there.
- **Some promises are `manual`.** They name what verifies them and why a machine
  cannot. See the status legend in [the specification](product/README.md).

---

## Where to read next

- [The product specification](product/README.md) — what the product promises,
  and what the statuses mean
- [The scenario suite](../tests/scenarios/README.md) — how to run it and how it
  is built
- [Scenario conventions](../tests/scenarios/CONVENTIONS.md) — the standard a new
  scenario is held to, and the ways a test can pass while proving nothing
- [The live lane](../tests/scenarios/LIVE.md) — running against real providers
- [Backend testing](../lemma-backend/README.md#testing) — markers, modes, and
  the e2e gate
