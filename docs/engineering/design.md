# Design and abstraction

How code is shaped here: what a module may know about another module, when an
abstraction earns its place, and where the seams go.

Rules carry ids (`DES-01`) so a review comment, a lint message, or a commit can
cite one. Each names the check that enforces it and the count that check reports
today — a rule with a non-zero count is a **ratchet**: the number may fall, never
rise. Rules at zero are **hard**: any new instance fails the build.

Counts were measured on the tree at the time of writing. Re-measure with the
command in the rule rather than trusting the number.

---

## The shape

Lemma's backend is a **modular monolith with a declarative registry and
event-carried collaboration**. Fourteen modules are listed in
`app/core/registry/installed.py`; each declares its routers, consumers, tasks and
lifespan hooks and nothing else registers centrally.

Three ideas hold it together, and they are worth stating because most design
questions here resolve to one of them:

**A module is a bounded thing with a published surface.** Its `contracts` package
and its domain events are that surface. Everything else — services, repositories,
ORM models, controllers — is private, whatever Python's import system permits.

**A dependency points at a type you own.** When a module needs something another
module or a vendor provides, it declares a `Protocol` it can be satisfied with,
and something outside both binds an implementation to it. The consumer names the
shape; the provider fits it.

**Collaboration that can be asynchronous should be.** A domain event, written in
the same transaction as the state it describes and consumed through an
idempotent inbox, outlives the process that emitted it. A direct call does not.

### The canonical example

One capability crosses a module boundary end to end and does it correctly. Read
these four files before adding a cross-module dependency of your own — this is
the recipe:

| Step | File |
|---|---|
| The port, declared where the consumer can own it | `app/core/ports/widget_content.py:26` |
| The implementation, in the providing module | `app/modules/agent/services/widget_asset_service.py:21` |
| The binding, published as a factory by the provider | `app/modules/agent/contracts/widget_content.py:26` |
| The consumer, depending on the type and not the class | `app/modules/apps/api/dependencies.py:82` |

---

## Boundaries

### DES-01 — a module reaches another module only through `contracts` or a domain event

Not its services, not its repositories, not its ORM models, not its
`api/dependencies`.

*Why:* an import of `other.services.X` is a promise you did not know you were
making. It survives refactors by preventing them.

*Check:* `uv run python scripts/check_architecture.py` (`forbidden_imports`)
*Today:* **34** — ratchet

### DES-02 — the composition root composes; it does not re-export

`app/composition/` is being emptied, and DES-03 below is the rule that replaces
it: a capability belongs to the module that provides it, published from that
module's `contracts/`. A file there whose whole body is `from … import X` with an
`__all__` is coupling with a nicer address — `app/composition/surface_agent.py`
is the last one, and its docstring says what it is waiting on.

*Check:* `check_architecture.py` (`composition_deep_imports`)
*Today:* **32** composition→module imports reach past `contracts` — ratchet

### DES-03 — a module must not import the composition root

Dependencies point inward. When a module imports `app.composition`, the root is
no longer a root; it is a shared middle layer that every module is coupled to.

*Check:* `make architecture` — `module_composition_imports` in the baseline.
*Today:* **25** — ratchet

Until this rule had a check, the number was a `grep` in this document and nothing
failed when it rose. Worse, the cycles those edges carried were invisible too:
`module_cycles` reads **0** because a file under `app/composition` is excluded
from the dependency graph, so a cycle with a hop through the root is not a cycle
as far as the gate is concerned. `induced_module_cycles` inlines that hop.
It reports **one component of 13 of the 15 modules**, and that is the number
this rule is really about — deleting the root without breaking those cycles
first would turn the whole backend into one knot.

### DES-04 — `app/core/` must not import `app/modules/`

Core is what modules are built on. The one exception is
`app/core/registry/installed.py`, whose job is naming them.

Today the central authorization service imports eight modules' ORM classes. The
gate could not see any of it until recently: it scanned `app/modules/` only,
which is how the count reached 42 without anyone deciding on it.

*Check:* `check_architecture.py` (`core_module_imports`), which exempts
`app/core/registry/installed.py`
*Today:* **40** — ratchet

### DES-05 — layers point one way

`services/`, `application/` and `domain/` must not import from `api/`; `api/`
must not import ORM models. A controller sees entities and schemas; a service
sees domain types.

*Check:*
```bash
grep -rnE "from app\.modules\.[a-z_]+\.api" app/modules/*/services app/modules/*/domain app/modules/*/application --include='*.py' | grep -v "/tests/" | wc -l
grep -rn "infrastructure.models" app/modules/*/api --include='*.py' | grep -v "/tests/" | wc -l
```
*Today:* **8** and **6** — ratchet

---

## Ports and adapters

### The art of it

A port is a promise stated by the code that needs something, in terms that code
understands. That framing decides almost every question about them.

**Introduce a port when the consumer would otherwise name a provider.** Crossing
a module boundary, calling a vendor SDK, touching the network, the clock, or the
filesystem — each is a place where the consumer's logic is about *what* it needs
and the provider's is about *how*.

**Do not introduce one to be abstract.** A `Protocol` with one implementation, no
test double, and no second implementation in prospect is a rename with ceremony.
The test is whether you can write a useful fake in ten lines; if the fake would
be as complicated as the real thing, the port is drawn in the wrong place.

**Draw it around a use case, not around a table.** A port with 38 methods
(`app/modules/agent/domain/ports.py:93`) is a repository wearing a Protocol's
clothes: every consumer depends on all of it, and no fake is writable. Two
consumers wanting different subsets is the signal to split.

**The consumer owns it.** A port declared in the providing module and imported by
the consumer is a dependency with extra steps. Declare it where it is needed —
`app/core/ports/` for cross-module, `domain/ports.py` for module-local.

### DES-06 — a port is a `Protocol` with six methods or fewer

Prefer `Protocol` to `ABC` (97 to 19 today), and put it in `domain/ports.py` —
not `interfaces.py`, not `providers/base.py`, not buried in an infrastructure
file.

*Check:* a script that reports every Protocol/ABC with its method count.
*Today:* **27** of 116 ports exceed six methods; the largest has **38** — ratchet

### DES-07 — a port signature carries no `Any`, no bare container, and no framework type

No `AsyncSession`, no `Request`/`Response`, no settings object, no ORM model. A
port that names a framework type has bound its consumer to that framework.

*Why this one matters most:* the port is exactly where the checker is the only
thing standing between two pieces of code. `Any` there costs more than `Any`
anywhere else. See [types.md](types.md).

*Check:*
```bash
grep -rnE "\bAny\b" app/modules/*/domain/ports.py app/core/ports/*.py | wc -l
```
*Today:* **59** `Any` lines across 5 port files — ratchet

### DES-08 — third-party SDKs and HTTP clients are constructed under `infrastructure/`

An adapter is the only place a vendor's name appears. A service that builds an
`httpx.AsyncClient` has an untestable dependency and usually an unbounded one —
the shared client at `app/core/net/http_client.py` already exists.

*Check:*
```bash
grep -rn "httpx.AsyncClient(" app/modules --include='*.py' | grep -v "/tests/" | grep -v "/infrastructure/" | wc -l
```
*Today:* **37** outside `infrastructure/`, **29** of them in
`services`/`application`/`domain` — ratchet

---

## Services

### DES-09 — no file over 600 lines anywhere under `app/`

Not a style preference: every service in this repo that passed roughly 600 lines
also acquired a state bug that a reviewer had to reconstruct the whole file to
find. The current gate applies the rule to `app/modules/` only, which is how
`app/composition/analytics_consumer.py` reached 975 lines unnoticed.

*Check:* `check_architecture.py` (`oversized_files`)
*Today:* **23** — 14 under `app/modules/` and 9 that only became visible when the
gate was widened past it, including `app/composition/analytics_consumer.py` at
975 lines — ratchet

### DES-10 — split a service by use case, not by layer

When a service grows past the limit, the wrong fix is `connector_service_2.py`
and the right one is to name the operations. `connector_service.py` (1,411
lines) is six use cases — install, provision, rotate credentials, discover
operations, execute, revoke — that share a repository and nothing else.

Split so that each new unit has one reason to change, its own narrow port set,
and a name that is a verb phrase from the product vocabulary.

### DES-11 — no process-global mutable state

A module-level `_thing: X | None = None` with a setter is last-writer-wins for
the whole process. It makes tests order-dependent and replicas non-deterministic.
State that must live for the process lifetime is built in a lifespan hook and
handed to the things that need it; where a global is genuinely unavoidable it
ships a `reset_*()` and the tests use it.

`app/modules/usage/services/usage_limit_provider.py:24` and
`agent/services/subscription_models_provider.py:17` are the pattern to stop
copying.

*Check:*
```bash
grep -rnE "^_[a-zA-Z_]+(: [^=]+)? = (None|\{\}|\[\])$" app --include='*.py' | grep -v "/tests/" | wc -l
```
*Today:* **76** module-level mutable globals, **101** `global` statements, **15**
`lru_cache` singletons — review; no gate reads these yet. `lru_cache` on an immutable object singleton
stays allowed; see the caching rule in
[development.md](../../lemma-backend/docs/development.md#caching).

---

## Shapes

### DES-12 — three shapes, and the boundaries between them are real

| Shape | Lives in | Crosses |
|---|---|---|
| ORM model | `infrastructure/models.py` | never leaves the repository |
| Domain entity | `domain/` | services, use cases, ports |
| API schema | `api/schemas/` | the wire, and nothing inward |

A `.model_dump()` handed across a boundary is all three collapsed into one
untyped dict. When two shapes really are identical, write the mapper anyway — it
is four lines, and it is the thing that lets the wire format change without a
migration.

### DES-13 — `contracts/` holds domain DTOs and mappers, never API schemas

`agent_surfaces/contracts/__init__.py:3` and `workflow/contracts/__init__.py:3`
currently re-export API pydantic schemas, which welds the wire format to
cross-module collaboration: an API-shape change becomes a cross-module break.

*Today:* **2** modules — hard once fixed

### DES-14 — one pagination type

Forty-three services declare their own `limit`/`offset` defaults, which is why
`record.list` has no maximum and the "waiting on me" inbox is unbounded. One
shared `PageRequest` with a maximum, applied at the boundary.

*Check:*
```bash
grep -rn "limit: int = \|offset: int = " app/modules/*/services/*.py app/modules/*/application/*.py | grep -v "/tests/" | wc -l
```
*Today:* **43** — ratchet

---

## Events

### DES-15 — every cross-module consumer is inbox-backed and idempotent

Redis Streams and streaq are at-least-once. A consumer that is not idempotent is
a duplicate side effect waiting for a redelivery, and the redelivery arrives on
the day the system is already under stress.

Persist state and its domain event in one transaction through the unit of work,
and consume through `InboxConsumer`.

*Check:*
```bash
grep -rln "inbox" app/modules/*/events/*.py | wc -l   # vs the number of handler files
```
*Today:* **7** of 14 handler files — ratchet

### DES-16 — capture context where it exists; never re-derive it in the consumer

`DomainEvent` carries lineage and W3C trace context at construction
(`app/core/domain/events.py:37`). A consumer that reconstructs "who did this"
from the payload will get it wrong the first time the event is replayed.

---

## The module skeleton

Every module has this shape, and an unknown top-level directory is an error:

```
app/modules/<name>/
  module.py            # the LemmaModule declaration
  api/                 # controllers, schemas, dependencies
  application/         # use cases
  services/            # domain services
  domain/              # entities, value objects, ports.py, events
  infrastructure/      # models.py, repositories, adapters
  contracts/           # the published surface
  events/              # consumers
  tests/               # unit/, e2e/
```

*Today:* **11** structural deviations; **2** modules have no `contracts/`;
`agent/tools/`, `agent_surfaces/platforms/` and `workspace/providers/` are three
names for "adapter". Pick one and DES-08 becomes a single grep.

---

## Adding a module or a cross-module capability

1. Write the port in the consumer, as a `Protocol` with the methods you actually
   call (DES-06, DES-07).
2. Implement it in the providing module, under `services/` or `infrastructure/`.
3. Publish a factory for it from the provider's `contracts/`, in a named
   submodule — never a re-export of the implementation (DES-02). A port held for
   the length of a run is the one case a factory beats free functions; anything
   else should be operations.
4. Depend on the type in the consumer, and bind the factory in its
   `api/dependencies.py`; never import the implementation (DES-01).
5. If the collaboration can tolerate latency, use a domain event instead of steps
   1–4, and make the consumer idempotent (DES-15).

## Related

- [types.md](types.md) — what the signatures in these ports must look like
- [tests.md](tests.md) — fakes implement the port; they do not patch it
- [Backend development guidelines](../../lemma-backend/docs/development.md) — DB
  sessions, caching, errors, logging, authorization, secrets
- [Module guide](../../lemma-backend/docs/modules/README.md) — what each module owns
