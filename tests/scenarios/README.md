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
cd tests/scenarios && uv run pytest --base-url http://localhost:8710
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

Two things are not Lemma: the model behind an agent, and the messaging platform
behind a surface. They are handled differently now, and one of them is on its
way out.

**The model is real.** Scenarios that drive an agent say what a person would say
and assert on what must be true afterwards — the row is still there, an approval
was raised, the work completed. They take `needs(MODEL_IS_REAL)` and skip with a
reason where no model is configured.

There used to be a seam for scripting the model's turns through a conversation's
`metadata`, and it is gone. It proved Lemma refused *that call*; it could not
prove a person typing a sentence ended up refused. Against a deployment it was
worse: `e2e_llm_mode` is `real` there, so the script was ignored in silence and
the scenario asserted a scripted model's behaviour against a thinking one — one
scenario in the live lane had been doing exactly that, and passing, for months.
The agent is *told how to behave* with an `instruction`, which is what a person
does when they set one up, and what it then does is the thing under test.

**The platform is real, and recorded.** Everything Lemma sends outward goes
through one proxy ([`harness/egress.py`](harness/egress.py)):

```bash
make scenarios-record CASSETTE=connectors   # real providers, real credentials
make scenarios-replay CASSETTE=connectors   # what they said, and nothing else
```

Recording drives the real Telegram, Google, GitHub and Slack and writes what
happened to [`cassettes/`](cassettes/). Replay serves that back and **kills any
request it has not seen**, so a replay run cannot quietly reach the internet and
pass for the wrong reason. The product is given the proxy's certificate
authority and no other, so a client that bypasses the proxy fails loudly rather
than succeeding against a real provider behind the suite's back.

No product change was needed for any of it: every outbound client is `httpx`
with `trust_env` left on, and `slack_sdk` loads the same variables itself.

The proxy is also where a scenario asks **what Lemma sent**:

```python
async def test_...(egress):
    ...
    [call] = egress.calls_to("api.telegram.org", path_contains="sendMessage")
    assert "approve" in call.json_body()["text"].lower()
```

That one query replaced three different recorder objects with three different
shapes, one per platform.

**Why recorded rather than imitated.** A stand-in we write ourselves cannot tell
us it has drifted: when Telegram changes a response, our imitation keeps
returning the old one and the suite stays green. A recording can — re-record it
and the diff is the news. That is why the cassettes are committed and reviewed.

**What is left of the old loopback fakes.** `harness/fake_upstreams.py` is being
retired one journey at a time, and a guard stops new callers appearing while it
goes. One piece will stay: a small server that returns a 500 on demand and hangs
past the outbound timeout. No real provider does that reliably, and the three
scenarios that need it are testing *Lemma's* error handling — the thing on the
other end only has to be some HTTP server behaving badly.

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

### One mailbox, a whole cast

The cast's addresses default to `example.com`, which is reserved and delivers
nowhere. That is right until an email surface answers one of them: the agent
replies to whoever wrote in, and against a real provider that reply is a hard
bounce at a domain that can never accept it — charged to the sending reputation
of an account the product itself uses.

```bash
SCENARIOS_MAILBOX=you@gmail.example      # in the environment, or the backend .env
```

Every colleague is then sub-addressed out of it — `you+priya.raman@…`,
`you+daniel.okonkwo@…` — so one inbox covers the cast, every address is
distinct, and a reply is something you can go and read.

Two rules the suite enforces rather than trusts:

- **The mailbox is never written down.** This is a public repository, and a real
  address committed to it is somebody's inbox for as long as the history exists.
  `test_no_real_address_is_hardcoded` fails on any address whose domain is not a
  reserved one.
- **It may not be the Resend inbound domain.** Every address there routes into a
  pod surface, so the cast would be writing to itself.

### Keeping it between runs

A stack the suite boots throws its database away at the end, which is right for
CI and wrong the moment a real connector is involved. GitHub, Slack and Gmail
accounts exist only after a person consented in a browser, and the product has
no way to store one without that — correctly. So a throwaway database discards
the one thing the suite cannot recreate for itself, and every re-run means
asking somebody to click through OAuth again.

```bash
SCENARIOS_STANDING_STACK=1 make scenarios     # the same containers, next time too
make scenarios-standing-down                  # and remove them
```

The containers get fixed names, the database a named volume, and none of them is
torn down at the end. Two things had to be true for this to work, and both were
checked rather than assumed:

- **Supertokens needs storage of its own.** With no `POSTGRESQL_CONNECTION_URI`
  that image keeps everything in memory, so a persisted application database
  would come back with every password gone — users intact and nobody able to
  sign in, which is worse than not persisting at all. It gets its own database
  in the same Postgres now, so `alembic downgrade` and the sweep cannot reach it.
- **The encryption key has to be the same key.** Credentials are Fernet blobs;
  a key regenerated per boot turns every stored account into noise. In
  local/testing it is derived from a fixed seed, so it is stable — verified by
  storing an API key on one boot and executing an operation with it on the next.

What this buys is the point of the standing tenant: consent once, then run the
suite as often as you like — and somebody else can run it too, without being
sent to a browser first.

## The lanes

Four, not two — [LIVE.md](LIVE.md) uses "the two lanes" for the fast/live pair
and this used to use it for fast/sandbox, which made the same phrase mean two
things.

`make scenarios` is the **fast lane**: everything runs against containers the
stack starts itself, in about **90 seconds** including boot. That is deliberate
— a suite that is slow enough to think about is a suite people stop running.

`make scenarios-sandbox` is the **sandbox lane**. Creating a function is not a
metadata write: the API provisions a sandbox and extracts the declared schemas
by loading the code inside it, so those scenarios need the workspace and
function images built first. They are marked `@pytest.mark.sandbox` and
deselected by default.

`make scenarios-live` is the **live lane** — real providers, real model. It has
[its own document](LIVE.md), because what it can and cannot do unattended is
most of what there is to say about it.

`make scenarios-guards` is the **guard lane**: the rules the suite holds itself
to, no Docker, ~20ms.

### Running the whole suite locally

Every journey shares one stack, which is not the shape CI runs (it shards by
journey). Give it the replica shape the product is built for:

```bash
SCENARIOS_WORKERS=3 make scenarios
```

`SCENARIOS_WORKERS` is how many worker processes the stack boots — one unless
asked. `schedule_poller` says "Every replica runs this. Nothing elects a leader;
the claim decides who fires", and one worker draining several hundred queued
agent runs through a single event loop is how a scenario ends up waiting on a
reply that is merely behind a queue.

It is **refused above 1 when a polling receiver is on** — Telegram answers a
second `getUpdates` for the same bot with 409 Conflict and the two pollers take
turns losing messages. That is also worth knowing outside the suite: two Lemma
backends on one machine sharing a `.env` will fight over the same bot, and the
symptom is a surface message that is simply never answered.

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
