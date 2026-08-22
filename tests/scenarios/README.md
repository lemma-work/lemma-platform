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
| `harness/environment.py` | Asks the target what it is configured to do, and whether this run may write to it |
| `harness/tenant.py` | Who the standing cast are, and what they are to each other |
| `harness/provision.py` | Builds that tenant on a deployment, or puts it back |
| `harness/run.py` | The mark one run leaves on a tenant shared with every other run |
| `harness/world.py` | `World` and `Person` — a scenario asks the world for people, and people do things |
| `harness/steps/` | The product verbs, one module per noun. `alice.creates_a_pod(...)` |
| `harness/drivers/api.py` | The only place that knows about paths, verbs and status codes |
| `harness/markers.py` | `@journey`, `@capability`, `@scenario`, `@proves`, `@covers` |
| `harness/reporting.py` | Turns those marks into the journey tree above |
| `journeys/` | The scenarios themselves, one directory per journey |

## The four rules

Enforced by `journeys/test_harness_contract.py`, which runs first and needs
nothing booted. [CONVENTIONS.md](CONVENTIONS.md) is the full standard — why each
rule exists, the review checklist, and a catalogue of the ways a scenario can
pass while proving nothing.

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
- **Create everything you assert on, and name it through `run.name()`.** The
  stack is shared across the session and the tenant is shared across every run,
  so another scenario's pods — and last night's — are in the same database.
  Never assert on a total; filter to what this run made.
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

## Standing in for other people's servers

Two things are not Lemma and cannot be run for real on every change: the model
behind an agent, and the messaging platform behind a surface. Both are stood in
for, and in both cases through a **supported product setting** rather than a
patch:

- **The model** — `E2E_LLM_MODE=mock` swaps in a deterministic scripted model.
  The production code path runs to the model boundary.
- **The platform** — `harness/fake_platform.py` is a small HTTP server that
  answers as Telegram. A scenario points a surface at it with `api_base_url` on
  the connected account, which exists so a deployment can use a self-hosted Bot
  API server. Lemma itself runs entirely for real: it registers the webhook,
  verifies the secret on delivery, resolves the sender, runs the agent, and
  sends the reply — and the fake records what it said.

Everything else is real, including the Docker sandboxes that functions execute
in.

## Measuring it

Three different numbers, and they answer three different questions:

```bash
make quality                    # promises covered, and API/event surface touched
```
```bash
make scenarios-code-coverage    # what the backend actually executes
```

The last one instruments the uvicorn and worker subprocesses, so it measures
the product being driven over HTTP rather than functions being called directly.
It is off by default because measuring costs runtime and this suite is meant to
be run constantly.

## What is not here yet

Being honest about the edges, because a half-built harness that looks finished
is worse than one that says where it stops:

- **No event assertions.** `@covers` naming an event records intent rather than
  proving delivery. `analytics_host` is configurable, so a capture server would
  close this — it is how `DEV-ONB-004` should have been caught rather than by
  reading a worker log.
- **Only Telegram is stood in for.** Slack, Teams, WhatsApp and the email
  surfaces each need their own fake; the shape is there to copy.
- **Publishing a bundle to GitHub** needs a connected account at a real
  provider. Export and import are covered end to end.
- **The client conformance is a subset**, not a mirror of every journey. A
  process per call is too slow for that, and the point is that the clients
  agree on the core path.

## The standing tenant

Most scenarios do not want a stranger. They want somebody who already works
somewhere, in a pod that already has things in it — which is the situation every
real user is in, and the one a suite that starts the world over for every test
can never reach.

So there is a **cast**: five colleagues at Vantage Freight, plus Hannah at
Calder Retail, who is the outsider every refusal scenario needs. They are
declared in [`harness/tenant.py`](harness/tenant.py) and they sign **in**:

```python
daniel = await world.person("daniel")          # already here, already ORG_EDITOR
pod = await daniel.works_in("sales")           # opens it; makes it only if absent
table = await daniel.creates_a_table(named=run.name("orders"), in_pod=pod)
```

`world.new_person()` is still there for a scenario that genuinely needs somebody
brand new — onboarding, invitations, being refused as a stranger.

**Everything durable is named through `run.name()`.** The tenant is shared with
every run before and after this one, so `orders` alone collides and `orders` in a
pod holding forty other tables cannot be asserted on. `run.name("orders")` gives
`orders_scn7f3a1`: an assertion filters to it, cleanup can tell the suite's
leavings from a person's work, and a failure says which run to go and look at.

Against a deployment, the tenant is built once and deliberately, by a person:

```bash
make scenarios-provision TARGET=https://your-lemma
make scenarios-deployment TARGET=https://your-lemma
```

A run never registers anybody. That is what lets the same suite run against a
deployment whose signup gates are on — signing in passes none of them — and it
is why a run leaves no new organizations behind, which matters because the
product has no way to delete one. A stack the suite boots itself is the
exception: it starts empty, so the tenant is built in it on first use.

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
