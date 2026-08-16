# Product scenario suite

The suite that answers "does Lemma do what we say it does?"

Every test here proves a numbered promise in
[the product specification](../../docs/product/README.md), runs against a real
Lemma over a real socket, and reports itself in product language:

```
Getting started
  Sign up and create an organization
    ✓  A new person signs up and becomes a known user            [PS-ONB-001]  1.4s
    ✗  The person who creates an organization owns it            [PS-ONB-010]  0.9s
```

## Running it

```bash
make scenarios
```

Needs Docker — the suite starts Postgres, Redis and SuperTokens, migrates the
database, and runs the backend under uvicorn. First run pulls images.

While writing scenarios, the guards are the fast loop — no Docker, no stack,
about twenty milliseconds:

```bash
make scenarios-guards
```

To iterate against a Lemma you are already running:

```bash
cd tests/scenarios && uv run pytest --base-url http://localhost:8000
```

## How it is put together

| Piece | What it does |
|---|---|
| `harness/stack.py` | Boots the system under test and hands back a URL |
| `harness/world.py` | `World` and `Person` — a scenario asks the world for people, and people do things |
| `harness/steps/` | The product verbs, one module per noun. `alice.creates_a_pod(...)` |
| `harness/drivers/api.py` | The only place that knows about paths, verbs and status codes |
| `harness/markers.py` | `@journey`, `@capability`, `@scenario`, `@proves`, `@covers` |
| `harness/reporting.py` | Turns those marks into the journey tree above |
| `journeys/` | The scenarios themselves, one directory per journey |

## The four rules

Enforced by `journeys/test_harness_contract.py`, which runs first and needs
nothing booted.

**Nothing imports the backend.** The suite reaches Lemma only over HTTP. An
import of `app.*` would make this a unit test in a black-box costume — able to
pass against code paths no real client can reach.

**Nothing is mocked.** No `monkeypatch`, no `AsyncMock`, no `patch`. The only
substitutions are the ones the stack itself is booted with, chosen in
`harness/stack.py` where everyone can see them. This is the rule that separates
this suite from the module suites, where 3,600 patch sites make a passing test a
statement about the mocks.

**Nothing sleeps.** A sleep is either too short, and flakes under load, or too
long, and everyone pays for it every run. Wait on a condition.

**Every test says what it proves.** `@scenario` and `@proves` are required. A
test without them runs, passes, and tells nobody anything.

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

Four things to hold to:

- **`@proves` names a promise that exists.** `make quality` fails otherwise, and
  fails again if a promise marked `covered` has nothing proving it.
- **Steps are product verbs.** If a scenario contains a path or a status code,
  the step is missing — add it to `harness/steps/` instead. That is what will
  let these same scenarios run through the CLI and the SDKs.
- **Create everything you assert on.** The stack is shared across the session,
  so another scenario's pods are in the same database. Never assert on a total.
  `world.new_person()` makes uniqueness the default.
- **Move the promise to `covered` in the same pull request**, once you have seen
  it pass. If it does not pass, the finding is a `gap` in the specification and
  a fix to the code — not an edit to the assertion.

## The clients we ship

`journeys/clients/` runs the same core journey through the **CLI**, the
**Python SDK** and the **TypeScript SDK**, each in its own environment as a
subprocess — `uv run lemma pods list` is the product; importing the CLI's
internals is not.

This is where a client's own bugs surface. A green API suite says the server
works; it says nothing about whether the CLI maps its arguments correctly or
whether the TypeScript build is loadable. `DEV-SDK-001` — the built TS SDK
cannot be imported from Node at all — was found here and by nothing else.

## What is not here yet

Being honest about the edges, because a half-built harness that looks finished
is worse than one that says where it stops:

- **No event assertions.** `analytics_host` is configurable, so a capture server
  would let scenarios assert on the real product-analytics contract black-box.
  Until then, `@covers` naming an event records intent rather than proving
  delivery — which is how `DEV-ONB-004` went unnoticed until a worker log was
  read by hand.
- **Nothing crosses a surface end to end.** Surface setup, webhook ingestion and
  delivery need a platform fake per adapter. The inbox side is covered.
- **Import is untested.** Export is; a full export → import round trip needs the
  bundle staged and applied, which is the next increment.
- **The client conformance is a subset**, not a mirror of every journey. A
  process per call is too slow for that, and the point is that the clients
  agree on the core path.

## The two lanes

`make scenarios` is the fast lane: everything runs against containers the stack
starts itself, in about **90 seconds** including boot. That is deliberate — a
suite that is slow enough to think about is a suite people stop running.

`make scenarios-sandbox` is the slow lane. Creating a function is not a metadata
write: the API provisions a sandbox and extracts the declared schemas by loading
the code inside it, so those scenarios need the workspace and function images
built first. They are marked `@pytest.mark.sandbox` and deselected by default.

## When a scenario finds a bug

That is the suite working. Do not soften the assertion.

1. Verify it by reading the code — a scenario failing is evidence, not a
   diagnosis, and three of the findings in [`issues.md`](../../issues.md) turned
   out to be the *scenario* being wrong about the product.
2. If the product is right and the scenario was wrong, fix the scenario and
   sharpen the promise in `docs/product` so the next person does not repeat it.
3. If the product is wrong, add an entry to [`issues.md`](../../issues.md), mark
   the promise `gap`, and leave the scenario failing — as
   `@pytest.mark.xfail(strict=True)` so that fixing the bug turns the build red
   until someone removes the marker.
