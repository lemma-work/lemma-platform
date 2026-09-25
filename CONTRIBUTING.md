# Contributing to Lemma

Thanks for helping improve Lemma. Keep pull requests focused, explain user and
operator impact, and add tests for behavioral changes.

New to the codebase? [ARCHITECTURE.md](ARCHITECTURE.md) is the map — components,
where state lives, and the invariants that hold everywhere. Questions that
aren't a bug report belong in
[Discussions](https://github.com/lemma-work/lemma-platform/discussions); see
[SUPPORT.md](SUPPORT.md).

## Get the stack running

From a checkout, hot-reload from source:

```bash
make init        # create .env files with local defaults (idempotent)
make dev         # infra + backend + frontend
```

`make help` lists everything else. The dev stack uses ports 3710 (harness) and
8710 (backend). Run `make dev-frontend` alongside it for the user-facing workspace
on port 3000. `make init` installs and configures both apps.

### Toolchain versions

Node is pinned by [`.nvmrc`](.nvmrc) at the repo root, and that file is the only
place the version is written. `nvm use` (or `fnm use`, or any tool that reads
`.nvmrc`) picks it up; CI reads the same file through `node-version-file`, both
`package.json` files declare it under `engines`, and the frontend Dockerfile
builds on the matching image. Change it in one place and everything follows.

Python is 3.14 for the backend, managed by `uv`.

Rust is pinned by [`rust-toolchain.toml`](rust-toolchain.toml) at the repo root,
the same way as `.nvmrc` above: rustup reads it, downloads that version if you
do not have it, and uses it for every crate here. CI installs the same version
explicitly, so the clippy that judges a pull request is the clippy you ran
before opening it — which is worth having, because clippy adds lints every six
weeks and one of them once turned CI red on a change containing no Rust at all.
Bumping it means moving the `channel` and the `dtolnay/rust-toolchain@<version>`
refs in the workflows together.

One 3.14 feature is worth calling out because it reads as a bug to anyone (or
anything) expecting older Python: [PEP 758][pep758] allows `except` to take an
unparenthesized tuple, so `except TypeError, ValueError:` is a two-type handler,
not the Python 2 `except E, name:` binding form. The codebase uses it. Review
bots trained on older syntax flag it as an error; it is valid, and `ruff` and
`mypy` on 3.14 both accept it.

[pep758]: https://peps.python.org/pep-0758/

## Find the right component

Each component has its own setup and its own checks. Run the ones you touched.

| You changed… | Read | Run before opening a PR |
|---|---|---|
| `lemma-backend/` | [backend README](lemma-backend/README.md), [development guidelines](lemma-backend/docs/development.md), [module guide](lemma-backend/docs/modules/README.md) | see below |
| `lemma-frontend/` | [frontend README](lemma-frontend/README.md) | `npm run check && npm test && npm run build` |
| `lemma-harness/` | [frontend README](lemma-harness/README.md), [frontend contributing](lemma-harness/CONTRIBUTING.md) | `npm run check && npm test` |
| `lemma-cli/` | [CLI README](lemma-cli/README.md), [conventions](lemma-cli/CONVENTIONS.md) | `make test && make lint` |
| `lemma-typescript/` | [SDK README](lemma-typescript/README.md) | `npm run build && npm test` |
| `lemma-python/` | [SDK README](lemma-python/README.md) | `uv run pytest` |
| `lemma-skills/` | [skills README](lemma-skills/README.md) | — |
| `desktop/` | [maintainer guide](desktop/README.md), [architecture](docs/architecture/desktop.md), the [Desktop test matrix](#desktop-test-matrix) | `make desktop-test && make desktop-lint`, plus the lane the matrix names |

## Desktop test matrix

`make desktop-test && make desktop-lint` is the floor for any `desktop/`
change, not the whole of it. Desktop is several processes on two sides of a
wire, and each seam has one lane that holds it. A change extends the lane for
the seam it touches and updates the document that describes that seam, in the
same pull request. The lanes, and what CI runs when, are in
[docs/testing.md](docs/testing.md#the-lanes-and-what-runs-when).

| You changed… | Extend | Update |
|---|---|---|
| How an ACP adapter's output becomes run events (`desktop/agent-host/src/normalize/`, `acp/`) | Rust unit tests in the crate, and the golden transcripts: re-record `tests/fixtures/acp/<adapter>@<version>/` with `record_transcript.py`, regenerate with `UPDATE_GOLDEN=1 cargo test -p lemma-agent-host --test normalize_golden`, and review the diff | [agent-host-events.md](docs/architecture/agent-host-events.md) |
| A pinned adapter in `desktop/agent-host/agent-adapters.lock.json` | A recording for the new version, or a reason in `tests/fixtures/acp/unrecorded.json`. `normalize_golden` refuses a bump with neither, and an excuse for a version no longer pinned | [agent-host-events.md](docs/architecture/agent-host-events.md) |
| A link frame, close code or body (`desktop/agent-host/src/link/`, `lemma-backend/app/modules/agent/domain/agent_host_link.py`) | `desktop/agent-host/tests/fixtures/wire_contract.json`, which both sides are held to (`--test wire_contract` in Rust, `test_agent_host_wire_contract.py` in the backend); the link tests on each side (`tests/link_control_e2e.rs`, `test_agent_host_link_*.py`) | [agent-host.md](docs/architecture/agent-host.md#the-link) |
| Run delivery, the outbox, leases, recovery or dispatch, on either side | Unit tests on the side you changed, the hermetic real-binary e2e (`test_agent_host_process_e2e.py`), and the chaos e2e (`test_agent_host_chaos_e2e.py`) when the change touches what survives a crash or a dropped link | [agent-host.md](docs/architecture/agent-host.md#delivery), [agent-host-events.md](docs/architecture/agent-host-events.md) |
| Host execution: op frames, the exec-server, Seatbelt, the host provider or run selection | Seatbelt tests on macOS (`tests/seatbelt.rs`, `src/host_exec/tests.rs`); backend unit (`test_host_execution_selection.py`, `test_agent_host_provider.py`, `test_host_routing.py`); `test_host_execution_link_e2e.py`, and `test_host_execution_binary_e2e.py` through the real binary | [desktop-host-execution.md](docs/architecture/desktop-host-execution.md), [desktop-security.md](docs/architecture/desktop-security.md), [provider-adapters.md](docs/architecture/sandbox/provider-adapters.md) |
| What the workspace asks of the shell (`lemma-frontend/src/desktop/`) | The frontend's tests (`npm test` in `lemma-frontend`), including `tests/desktop-ipc.test.ts`, which holds every command the page invokes to a grant in `desktop/capabilities/workspace.json` and a handler in `desktop/src/app.rs` | [desktop.md](docs/architecture/desktop.md#tauri-ipc-commands-and-who-may-call-them) |
| A Tauri command, a capability, or who may call it (`desktop/src/`, `desktop/capabilities/`) | The app crate's tests (every registered command is granted somewhere), `desktop/ui-tests`, and the IPC contract test above | [desktop.md](docs/architecture/desktop.md#tauri-ipc-commands-and-who-may-call-them), [desktop-security.md](docs/architecture/desktop-security.md) |
| Anything that runs when the app starts: launch, the hosted path, locald's supervision of the Agent Host, bundling | The launch smoke, `desktop/e2e/launch_smoke.py` (CI job "Desktop launch smoke") | [desktop.md](docs/architecture/desktop.md), [agent-host.md](docs/architecture/agent-host.md) |
| The chat's rendering of Agent Host runs | `make desktop-agent-host-browser-e2e` and its JSON ACP scenarios | [agent-host-events.md](docs/architecture/agent-host-events.md) |

The Agent Host lanes use scripted ACP agents
(`desktop/agent-host/tests/fixtures/scripted_acp_agent.py` and
`scenarios/*.json`), never a real provider, and the real `lemma-agent-host`
binary rather than a stand-in for it: a double proves the half you wrote, not
what the other side actually sends. A new failure mode gets a scenario there,
not a fake host.

## Backend

The backend requires Python 3.14, PostgreSQL, and Redis. From `lemma-backend`:

```bash
uv sync
uv run alembic upgrade head
make test-unit
make lint
make lint-async
make typecheck-critical
make architecture
```

[Testing strategy](docs/testing.md) covers which of the three suites a change
needs — unit, module e2e, or a product scenario — and what each one gates.

Use `make test-e2e-fast E2E_WORKERS=1` for the deterministic container-backed
suite. Real providers, model calls, and Docker sandbox tests are protected and
must never use personal or production credentials.

### Backend architecture rules

The short form is below. The full standard — every rule with an id, the check
that enforces it, and whether it is a hard failure or a ratchet — is in
[docs/engineering/](docs/engineering/README.md):
[design](docs/engineering/design.md), [types](docs/engineering/types.md),
[test design](docs/engineering/tests.md).

- The canonical module documentation lives in `lemma-backend/docs/modules`.
- Cross-module collaboration belongs in an explicit `contracts` package or a
  versioned domain event. Do not import another module's API, service,
  infrastructure, or persistence model from new code.
- Persist state and its domain event in one database transaction through the
  unit of work. Consumers must be inbox-backed and idempotent.
- Never hold a DB session across external I/O or a streaming body.
- Classify errors at process boundaries. Preserve cancellation, redact secrets,
  and never return or log raw provider exceptions.
- Add an Alembic upgrade and downgrade test for schema changes.
- Do not annotate with `Any`, or with a bare `dict`, `list` or `tuple`. Both
  say "this boundary is not checked", and the checker then cannot help at
  exactly the place two pieces of code meet. Parameterise the container, or
  name the shape: a `TypedDict`, a dataclass, a pydantic model, a `Protocol`
  for an object you only call methods on.

`Any` is legitimate in two cases only — data with no shape until it is
validated, and an untyped third-party library — and both are narrower than they
look. [types.md](docs/engineering/types.md#when-any-is-legitimate) states them,
and says what to do instead in the cases people reach for them in.

`make architecture` enforces most of this as a no-growth ratchet against
`architecture-baseline.json`: existing violations are tolerated, new ones are
not. The `Any` rule is ratcheted too, per module — there are thousands of
existing annotations and no plan to rewrite them at once, but the count only
goes down.

## Code and comments

This repository is public. Comments, docstrings, test names and migration prose
all ship to anyone who clones it, and they are read far more often than they are
rewritten.

**Say why, not what.** A comment that restates the line under it goes stale the
first time that line changes, and was never worth reading. Name the constraint,
the failure it prevents, or the alternative that was rejected and why. If what
you want to say is *what this does*, the fix is the code, not a comment: a named
intermediate, a smaller function, a type that makes the invalid state
unrepresentable. Prefer deleting a comment by making the code say it.

**Never commit production or operational data.** The reasoning behind a fix is
worth keeping; the telemetry that led you to it is not. Out of comments,
docstrings and test names:

- percentages of production traffic, and run, request, user, org or pod counts;
- p50/p95/p99 latencies, error rates, incident timestamps, log-line-per-day
  counts, and query-plan row counts measured against the live database;
- money — spend, cost per call, invoice figures;
- customer, org or pod names, real user identifiers, and internal hostnames,
  dashboards or ticket URLs.

State the shape instead. *"Most runs are the first of their conversation, so a
conversation-keyed cache was a near-guaranteed miss"* carries the whole argument
without publishing the traffic that proved it. Numbers that are **contract**
stay: configured limits, byte caps, character budgets, TTLs, retry counts,
timeouts, and third-party API ceilings. The test is whether the number tells a
reader how to change the code, or tells them how much traffic we serve.

**Keep the investigation out of the source.** Audit findings, review transcripts,
incident write-ups and "what I tried" narratives belong in the pull request that
does the work, not in the file it touches. What survives into the comment is the
conclusion and the reason it holds.

Concretely, when a comment explains a fix, three things get written by accident
and none of them is load-bearing:

- **the date** — "On <the day it happened> an agent asked…". A date in a
  comment is an incident timestamp or a note that is already stale. The
  failure is what matters, not when it happened;
- **the host** — the deployment, cluster or tenant it was observed on. Use
  `lemma.work` for the product and `example.com` / `example.test` for a
  stand-in. This applies to fixtures too: a deployment name leaks from test
  data exactly as well as from a sentence;
- **the play-by-play** — "it answered X, then five minutes later called back
  with Y". State the shape of the failure; the sequence belongs in the PR.

A useful test: rewrite the paragraph with the date, the host and the sequence
removed. If it still tells the next reader why the code is the way it is, that
shorter version was always the comment. It usually does — those details feel
like evidence while you are writing and read as noise a month later.

`make lint-public-prose` enforces the first two (`scripts/check_public_prose.py`,
run by `make quality`). Hostnames are refused anywhere in a file; dates only in
comments and docstrings, so sample content in a string or a fenced block is left
alone. A date that is genuinely public — an incorporation date in a footer — goes
in `lemma-backend/scripts/public-prose-allow.txt` with its reason. The
play-by-play is not machine-checkable and is on the reviewer.

## Configuration

Every setting is an environment variable declared on a `pydantic-settings`
class. [`docs/configuration.md`](docs/configuration.md) covers the ones an
operator sets and points at the classes for the rest. Adding a setting means
adding a field with a description and a default, not reading `os.environ`
directly.

## Generated code

Do not hand-edit OpenAPI client output. See the
[generated-code policy](docs/security/generated-code-policy.md), run both SDK
generation scripts, and commit the specification and resulting clients in the
same change.

## Documentation

Documentation is part of the change, not a follow-up.

- Docs describe **what exists today**. Plans, review findings, and migration
  narratives belong in the pull request that does the work — not in `docs/`.
  `docs/internal/` and `*.internal.md` are gitignored if you want one on disk
  while you write it.
- New reader-facing docs go in [`docs/`](docs/README.md) and get a row in its
  index. Component-specific detail stays next to the code.
- Relative links are checked; make sure yours resolve.
- Don't paste coverage percentages or benchmark numbers into prose. They go
  stale silently. Name the command that produces them instead.
- [Code and comments](#code-and-comments) applies here in full. `docs/` is the
  most-read part of a public repository: no production traffic figures, entity
  counts, latencies, error rates, costs, customer names or internal URLs, and no
  security finding that has not been fixed and released. Reproducible benchmark
  budgets and results are fine — name the command that reproduces them.

### The product specification is the exception

[`docs/product/`](docs/product/README.md) is **normative**, not descriptive: it
says what the product is meant to do. When it and the code disagree, the default
assumption is that the code is wrong.

- A change to what a person can do updates the specification in the same diff.
- If you find the system does not behave the way a scenario says, do not edit
  the scenario to match. Mark it `gap`, note how it diverges, and fix the code.
- Move a scenario to `covered` in the pull request that adds the test proving
  it, having watched it pass — not before.

`make quality` checks that every `@proves` names a promise that exists, that
every promise claiming coverage has a test, and that
[`coverage.md`](docs/product/coverage.md) is current.

## Pull requests

The [pull request template](.github/PULL_REQUEST_TEMPLATE.md) lists what a
change needs. A backend PR should include migration/API compatibility notes,
exact test commands, security implications, and rollback guidance.

Merge blockers: generated-client drift, architecture baseline growth, new broad
`except` clauses, coverage below the committed floor, and unresolved
high/critical security findings.

## Security

Report vulnerabilities privately according to [SECURITY.md](SECURITY.md) —
never in a public issue or pull request. Never commit real credentials, tokens,
customer payloads, or production dumps.

## Code of conduct

Participation is governed by our [code of conduct](CODE_OF_CONDUCT.md).
