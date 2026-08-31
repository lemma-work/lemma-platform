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

`make help` lists everything else. The dev stack uses ports 3710 (frontend) and
8710 (backend).

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
| `lemma-frontend/` | [frontend README](lemma-frontend/README.md), [frontend contributing](lemma-frontend/CONTRIBUTING.md) | `npm run check && npm test` |
| `lemma-cli/` | [CLI README](lemma-cli/README.md), [conventions](lemma-cli/CONVENTIONS.md) | `make test && make lint` |
| `lemma-typescript/` | [SDK README](lemma-typescript/README.md) | `npm run build && npm test` |
| `lemma-python/` | [SDK README](lemma-python/README.md) | `uv run pytest` |
| `lemma-skills/` | [skills README](lemma-skills/README.md) | — |
| `desktop/` | [maintainer guide](desktop/README.md), [architecture](docs/architecture/desktop.md) | `make desktop-test && make desktop-lint` |

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

`make architecture` enforces most of this as a no-growth ratchet against
`architecture-baseline.json`: existing violations are tolerated, new ones are
not.

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
