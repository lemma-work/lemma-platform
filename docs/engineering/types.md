# Types and data shapes

Write the type. This document says what that means in practice, where the
exceptions genuinely are, and what checks hold the line.

Ids are `TYP-NN`. Each rule names its check and today's count; a non-zero count
is a **ratchet** (may fall, never rise), zero is **hard**.

---

## Why this is the rule that pays

An annotation is not documentation. It is the only thing that notices when two
pieces of code stop agreeing — and it costs most at exactly the place they meet.

A helper taking `service: Any` and calling six methods on it *has* a type; it
just isn't written down. Rename one of those methods and nothing fails until
production. The same helper taking a six-method `Protocol` fails at the rename,
in the checker, in the pull request that caused it.

This is why the strongest form of the rule applies to **boundaries**: ports,
service signatures, event payloads, job arguments, anything crossing a process.
Inside one function, an untyped local costs almost nothing. Across a seam, it
costs the seam.

### The current position, honestly

| | backend `app/` | `lemma-cli` | `lemma-python` (hand-written) |
|---|---:|---:|---:|
| `Any` in annotations | 2,152 | 308 | 107 |
| of which `dict[str, Any]` | 1,354 | — | — |
| bare `dict`/`list`/`tuple` | 429 | — | — |
| unannotated parameters | 804 | 108 | — |
| missing return annotations | 421 | 64 | 12 |
| three-argument `getattr` | 516 | — | — |
| `TypedDict` declarations | **1** | 0 | — |
| type checker in CI | 23 hand-listed paths | **none** | **none** |

`basedpyright` at the configured `standard` level reports **1,188 errors over
1,297 files** for the whole backend, in 13 seconds. That is a budget a ratchet
can start from today — the run is cheaper than the ruff run beside it.

---

## When `Any` is legitimate

Two cases, and they are narrower than they look.

**Data with no shape until it is validated.** A provider's JSON, a webhook body,
a tenant-supplied payload. Say so in a comment, and **narrow it at the first
opportunity** — the `Any` ends at the parser, it does not travel.

**A third-party library that ships no types.** Say which library, and confine the
`Any` to the adapter that wraps it.

What is not legitimate is reaching for it because writing the type is work, and
what is never legitimate is `Any` for a collaborator you call methods on. That
has a type: a `Protocol` with the methods you call.

---

## Rules

### TYP-01 — no `Any` in an annotation, and no bare `dict`, `list` or `tuple`

Parameterise the container, or name the shape.

*Check:* `check_architecture.py` (`untyped_escapes`); ruff `ANN401` for explicit
`Any`
*Today:* 2,949 escapes counted by the ratchet, **527** `ANN401` sites — ratchet

### TYP-02 — the ratchet counts what is missing, not only what is written

Today the escape counter reads *written* annotations under `app/modules/`. Two
consequences, both perverse: deleting `: Any` improves the score, and `app/core`
and `app/composition` are outside the rule entirely.

*Fix:* scan `app/`, and count an unannotated parameter and a missing return as
escapes alongside written `Any`.
*Today:* **804** unannotated parameters, **421** missing returns, plus ~200
escapes in `core` + `composition` that nothing counts — becomes the new ratchet
floor

### TYP-03 — no `dict[str, Any]` crossing a boundary

A port signature, a service signature, an event payload, a job argument. Inside
a function or at the wire edge it is allowed, with a comment.

`connection_config: dict[str, Any] | None` is currently the connector *execution
contract* — every executor re-discovers what is in it.

*Check:*
```bash
grep -rn "dict\[str, Any\]" app/modules/*/domain/ app/modules/*/services/
```
*Today:* **1,354** total, **353** of them return types (`agent_surfaces` 696,
`connectors` 238) — ratchet

### TYP-04 — a JSON shape that has a shape gets a name

`TypedDict` for a wire shape, a frozen dataclass or pydantic model for an
internal one. The backend has exactly **one** `TypedDict` today and 1,354
`dict[str, Any]`; that ratio is the whole finding.

`lemma-python/lemma_sdk/types.py:1-13` is the model to copy — a recursive
`JsonValue`/`JsonObject` family with domain-named aliases (`RecordData`,
`FunctionInput`, `ConnectorPayload`). Adopt the same module in `app/core`.

*Check:* the count of named shapes rises; the escape count falls by at least one
per shape added.

### TYP-05 — no `Any` for a collaborator

A parameter you call methods on gets a `Protocol` declaring only the methods you
call. See [design.md DES-06](design.md#des-06--a-port-is-a-protocol-with-six-methods-or-fewer).

*Check:*
```bash
grep -rnE ": Any" app/ | grep -E "(service|repo|repository|client|adapter|gateway|session)"
```
*Today:* ~**105** sites — ratchet

### TYP-06 — a session or connection annotation names the real class

`AsyncSession`, not `Session`, never `Any`. `SurfaceRepository.session` is
annotated as the *synchronous* `Session`, which is why nothing in that file
type-checks and 36 errors follow from one line.

*Check:* `grep -rn "session: Any\|: Session =" app/`
*Today:* **8** — hard once fixed

### TYP-07 — a mixin declares what it consumes

A `class XMixin` that reads `self.foo` inherits a base or Protocol that declares
`foo`. Surface services are attribute-less mixins today, which produces **300**
attribute errors and means the largest module is effectively unchecked.

*Check:* `uv run basedpyright app/modules/agent_surfaces/services`
*Today:* **300** `reportAttributeAccessIssue` — ratchet

### TYP-08 — status, kind, role and mode are enums end to end

No `== "running"` on such a field, and the ORM column carries the enum type. A
string comparison is a typo that type-checks.

*Check:*
```bash
grep -rnE '\.(status|kind|role|mode|state)\s*[=!]=\s*"' app/ lemma_cli/
```
*Today:* **278** backend, **130** CLI (a superset — the precise grep is smaller)
— ratchet

### TYP-09 — a signature that can return nothing says so

Annotate `T | None`, and give callers that cannot proceed without it a `require_*`
variant that raises. A signature declaring `T` and returning `None` is worse than
no annotation: it disables the check that would have caught it.

*Check:* `uv run basedpyright app/modules/*/infrastructure/repositories`
(`reportReturnType`, already on)
*Today:* **2** (`workflow_repository.py:182`, `run_repository.py:105`) — hard

### TYP-10 — never return a successful-looking empty object on a parse failure

`return OAuthCredentials(access_token="")`
(`connectors/services/connector_service.py:1362`) turns an unparsable payload
into a credential that fails later, somewhere else, with no trace of where it
came from. Raise, or return `None` and let the caller decide.

### TYP-11 — no `extra="allow"` on a model that is persisted or holds credentials

Typos become data, and data becomes migration. `connectors/domain/account.py`
allows extras on four credential models.

*Check:* `grep -rn 'extra="allow"' app/`
*Today:* **4** on credential models — hard once fixed

### TYP-12 — validate third-party payloads at the boundary; do not probe them

`getattr(sdk_object, "field", default)` scattered through a service is a parser
smeared across a call graph. Parse once, into a named shape, in the adapter.

*Today:* **516** three-argument `getattr` (mcp_executor 20,
composio_auth_provider 19, pydantic_ai_history 18) — ratchet against an
allowlist of designated parser modules

### TYP-13 — `**kwargs: Any` never crosses a process boundary

Job payloads and queue arguments are models or `JsonValue`. A queue argument is
serialised, stored, and deserialised by a different process at a different
version; `Any` there is a schema you cannot migrate.

*Today:* **20** `**kwargs: Any` (3 cross-process), **63** unannotated `**kwargs`
— ratchet

### TYP-14 — a `# type: ignore` names its rule and its reason

`# type: ignore[attr-defined]  # <why>`. A bare ignore silences everything on the
line, including the error that arrives next year.

*Today:* backend **40** (mostly qualified already); CLI **86**, mostly
`[no-untyped-def]` — those should be fixed, not annotated — ratchet

### TYP-15 — a package that ships `py.typed` passes a type checker

`lemma-python` ships `py.typed` today and runs no type checker; its typed return
annotations sit over a transport that returns `Any`, so a user's checker learns
nothing true. Neither `lemma-cli` nor `lemma-python` has any `[tool.ruff]` rule
selection or `[tool.basedpyright]` section.

*Fix:* add `basedpyright` to each package's own checks. Start with
`reportMissingReturnType` over `lemma_sdk/resources/` — 12 sites, one pull
request.

---

## Turning the checker up

`typecheck-critical` covers 23 hand-listed paths and reports zero errors in four
seconds. That list is the best typing documentation in the repo because every
entry carries a comment naming the failure its absence caused — copy that habit
when you add one.

The path from here is a **per-module budget**, ratcheted like the architecture
baseline, not a repo-wide flag flip:

| Module | `standard` errors today | Realistic next step |
|---|---:|---|
| `pod`, `apps`, `function`, `usage`, `icon` | ≤ 30 | drive to 0, then pin `strict` |
| `workflow` | 18 | drive to 0 |
| `connectors` | 59 | ratchet |
| `agent_surfaces` | 412 (377 files) | fix TYP-06 and TYP-07 first; most of it is two lines |

Flags worth knowing, in the order they become affordable:

- **`reportMissingParameterType` / `reportMissingReturnType`** — highest value per
  unit of effort, and they close the TYP-02 loophole. Feasible per module now.
- **`reportMissingTypeArgument`** — this *is* the "no bare `dict`" rule.
  429 sites; feasible today for `pod`, `usage`, `apps`, `function`, `icon`.
- **`reportUnknownMemberType`** — blocked until TYP-06 and TYP-07 land, then
  immediately feasible for `agent_surfaces`.
- **`reportAny`** — not feasible repo-wide (2,152 sites). Feasible *per file* on
  `domain/ports.py`, which is where it pays most.

---

## Secrets

API keys and tokens are `SecretStr` end to end. Reveal plaintext only at the
point of use, through `reveal_secret` / `reveal_credentials` — never in a log,
never in a serialised payload, never in an error detail. See
[development.md](../../lemma-backend/docs/development.md#secrets).

## Related

- [design.md](design.md) — where these signatures live and what a port may say
- [tests.md](tests.md) — a fake is type-checked against the Protocol it stands for
