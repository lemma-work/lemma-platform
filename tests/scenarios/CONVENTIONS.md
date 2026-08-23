# Scenario conventions

The standard a scenario is held to. [README.md](README.md) is the tour — how to
run the suite and what the pieces are. This is what a reviewer checks, and why.

Read [docs/testing.md](../../docs/testing.md) first if you are deciding *whether*
your change needs a scenario at all. Most do not.

---

## What this suite is for

**Proving the product keeps a promise, to somebody outside it.**

Everything else follows from that. The suite drives Lemma the way the frontend,
the CLI and the SDKs do — over a socket, with no privileged access — because a
promise you can only verify from inside is not a promise to anybody. It reads in
product language because the people who need to know whether Lemma works are not
all going to read Python. And it is traced to a specification that can fail,
because a document that describes the code cannot tell you the code is wrong.

### The four rules

Enforced by `journeys/test_harness_contract.py`, which runs first and needs
nothing booted.

**Nothing imports the backend.** An import of `app.*` makes this a unit test in
a black-box costume — able to pass against code paths no real client can reach.

**Nothing is mocked.** No `monkeypatch`, no `AsyncMock`, no `patch`. The only
substitutions are the ones the stack is *booted* with, chosen in
`harness/stack.py` where everybody can see them. This is what separates this
suite from the module suites, where thousands of patch sites make a passing test
a statement about the mocks.

**Nothing sleeps.** A sleep is either too short and flakes under load, or too
long and everyone pays for it on every run. Wait on a condition.

**Every test says what it proves.** `@scenario` and `@proves` are required. A
test without them runs, passes, and tells nobody anything.

### And two the guards also enforce

**No scenario depends on the machine it runs on.** Not a developer's `.env`, not
which Docker images happen to be built. Both have happened; both are guarded now.

**No two steps share a name.** A silently shadowed step means one of them is
never called and its scenarios prove nothing.

---

## Writing one

```python
from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Getting started"), capability("Sign up and create an organization")]


@scenario("The person who creates an organization owns it")
@proves("PS-ONB-010")
@covers("org.create", "org.member.list", "organization.created")
async def test_creator_of_an_organization_owns_it(world):
    alice = await world.new_person("alice")

    organization = await alice.creates_an_organization()

    assert await alice.own_role_in(organization) == "ORG_OWNER"
```

- **`@proves` names a promise that exists.** `make quality` fails otherwise, and
  fails again if a promise marked `covered` has nothing proving it.
- **Steps are product verbs.** A path or a status code in a scenario body means
  a step is missing — add it to `harness/steps/`. That is also what will let
  these scenarios run through the CLI and the SDKs.
- **Create everything you assert on, and name it through `run.name()`.** The
  stack is shared across the session and the tenant is shared across every run
  that has ever touched it. Never assert on a total; filter to what this run
  made. See [the standing tenant](README.md#the-standing-tenant).
- **Move the promise to `covered` in the same pull request**, once you have
  watched it pass. If it does not pass, the finding is a `gap` and a fix to the
  code — never an edit to the assertion.

### Adding a step

Steps live in `harness/steps/`, one module per noun, mixed into `Person`. A step
should read as something a person does (`alice.creates_a_pod`), take product
nouns, and **fail loudly when its assumption breaks** rather than returning
something empty. `permissions_in` raises when the payload has no `actions` key
for exactly this reason — see below.

### Adding a journey

A new directory under `journeys/` needs a row in the CI matrix in
`.github/workflows/scenarios.yml`. `test_every_journey_runs_in_ci` fails the
build if you forget, because the alternative is a directory that passes locally,
shows green in CI because it was never selected, and is still reported as
covering its promises.

### The markers

| Marker | Meaning |
|---|---|
| `@journey`, `@capability` | Where this sits in the specification |
| `@scenario` | What it proves, as a sentence a person would say |
| `@proves` | The `PS-` ids it proves. Gated |
| `@covers` | Operation ids and events it exercises. Gated |
| `sandbox` | Needs the workspace images. Excluded from the fast lane |
| `live` | Needs real providers. Excluded from both. See [LIVE.md](LIVE.md) |

---

## How a scenario passes while proving nothing

Every entry below is a real defect from this suite's own history, caught only
because somebody went looking. They are the reason the review checklist exists.

**Reading a field that does not exist.** `permissions_in` read a key the API
never returns, so it returned an empty set — and every "holds no write
permission" assertion passed against nothing. A test that cannot fail is worse
than no test, because it occupies the space where a real one would go. *Steps
that read a payload must fail loudly when the shape is not what they expect.*

**Asserting on the wrong surface.** Three of these, each of which reported a
working feature as broken:
- The file *list* returns the root directory only, so an attachment landing in a
  folder is invisible to it. The tree is where to look.
- A filtered trigger produces no run — there was no work — so it is recorded on
  the schedule as its last fire status, not in the run history.
- A change frame carries its operation twice, as `operation` and inside the
  event `type`. Reading one of them depends on which the server filled in.

*Before concluding the product is broken, check you are asking the right
question.*

**Racing the thing under test.** A conversation is created before its message is
persisted, so reading messages immediately finds an empty thread. A change
stream anchors at connect time, so a write made just before can still be in
flight and "the next frame" is not the frame for the thing you just did.
*Wait for the state you are asserting on, not for a proxy that arrives first.*

**Passing for the wrong reason.** The subtlest kind, and the most dangerous:
- `answers_form` sent the wrong request body, so a "the run refuses this" test
  was passing on a 422 schema-validation error. It would have passed against a
  product with no rule at all.
- A last-admin test had an organization owner doing the demotion — refused for
  lacking the permission, which has nothing to do with the rule being tested.

*Ask: would this test still pass if the behaviour I care about were removed? If
yes, it is testing something else.*

**Depending on the environment.** A scenario that read a developer's `.env`
behaved differently on a laptop where Slack was configured — and the product was
right both times. Another provisioned a workspace and passed only on machines
that happened to have the sandbox images built. *A suite whose result depends on
whose machine it runs on cannot be trusted in either direction.*

**Claiming an event was delivered.** `@covers` naming an event records intent,
not proof. It means "this scenario drives the path that emits it". Without a
capture server it is not evidence the event arrived — one such event turned out
to be crashing its consumer on every single call, and was found by reading a
worker log rather than by any assertion.

---

## The review checklist

For a new or changed scenario:

- [ ] `@proves` names a promise that exists, and the promise's status matches
      reality — `covered` only if this passes today.
- [ ] **Would it fail if the behaviour regressed?** Not "does it pass".
- [ ] It asserts on something the product *tells a client*, not on internal
      state and not on a proxy that happens to arrive first.
- [ ] Every wait is a condition with a description, not a sleep and not a fixed
      poll count.
- [ ] It creates everything it asserts on, and asserts on nothing shared.
- [ ] The scenario body reads as product language — no paths, no status codes.
      Those live in steps.
- [ ] A refusal asserts the code that matters where the code matters. `>= 400`
      hides a 404 that became a 403, which is an existence leak.
- [ ] Any fixture that looks like a credential is built, not written down.
- [ ] If it found a bug: `issues.md` entry with a `DEV-` id, promise moved to
      `gap`, scenario marked `xfail(strict=True)` so the build reddens when the
      code is fixed.

---

## What does not belong here

- **Failure paths that need a dependency to break.** No mocking, so no way to
  make one. Those are module e2e tests.
- **Anything about one function or one class.** Unit tests.
- **Assertions about internal state** — a database row, an event log entry. If a
  client cannot see it, a scenario should not assert it.
- **Speed and load.** There are benchmark harnesses for that.

The counterpart to this list is in [docs/testing.md](../../docs/testing.md),
along with why the module e2e suite covers much of the same ground on purpose.
