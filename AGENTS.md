# Working in this repository

For anyone — person or agent — making a change here for the first time.

This file is a map, not a rulebook. The rules live in
[CONTRIBUTING.md](CONTRIBUTING.md) and the documents below; repeating them here
would only let the two drift apart.

## What is here

| Directory | What it is |
|---|---|
| `lemma-backend/` | The API, the worker, and every module. Python, FastAPI, Postgres |
| `lemma-frontend/` | The web workspace |
| `lemma-cli/`, `lemma-python/`, `lemma-typescript/` | The clients we ship |
| `desktop/` | The desktop app and the agent host |
| `docs/` | Documentation, indexed by [docs/README.md](docs/README.md) |
| `tests/scenarios/` | The black-box product suite |

Each component has its own checks. `CONTRIBUTING.md` has the table of what to
run for the one you touched.

## Running anything in the backend

Always through `uv`, from `lemma-backend/`:

```bash
uv run python ...
uv run pytest ...
```

Never bare `python3` or `pytest`. The backend is **Python 3.14**; `uv run` gets
you that, and on macOS a bare `python3` is Xcode's 3.9. The root
`.python-version` pins the version for `uv`, not for your `PATH`.

This is not a style preference. 3.14 accepts syntax 3.9 rejects — see the PEP 758
note under [Toolchain versions](CONTRIBUTING.md#toolchain-versions) — so the
wrong interpreter does not fail with "module not found", it reports a
`SyntaxError` in code that is correct and has been shipping for weeks.

## Read these before changing behaviour

- **[CONTRIBUTING.md](CONTRIBUTING.md)** — setup, architecture rules, and what a
  pull request needs.
- **[docs/engineering/](docs/engineering/README.md)** — the standard the code is
  held to: [design and abstraction](docs/engineering/design.md),
  [types](docs/engineering/types.md), [test design](docs/engineering/tests.md).
  Every rule carries an id, the check that enforces it, and whether it is a hard
  failure or a ratchet.
- **[docs/product/](docs/product/README.md)** — what the product promises, in
  product language, with stable ids. **It is normative:** it says what the
  product is *meant* to do, not what the code currently does.
- **[docs/testing.md](docs/testing.md)** — the three suites, which one your
  change needs, and what gates what.

## The six that get broken most

**The specification is not a description.** If the system does not behave the
way a scenario says, do not edit the scenario to match. Mark it `gap`, record
the divergence in [issues.md](issues.md), and fix the code. A specification that
cannot fail is documentation.

**Behaviour changes need a test, in the right suite.** A promise to a user gets
a scenario in `tests/scenarios/`. A failure path gets a module e2e test. One
function gets a unit test. `docs/testing.md` covers choosing.

**Never leave a background process behind.** Anything that spawns load — a
stress loop, a dev server, a CPU hog used to reproduce a flake — tears it down
from a `trap`, not from the last line of the script, which is not reached when
the command times out or the session ends. Orphans pin a core each until
somebody notices, and it has already happened more than once. Use
`desktop/scripts/stress_test_under_load.sh` rather than writing the loop again;
[docs/testing.md](docs/testing.md#rules-that-apply-everywhere) has the rule and
the two ways the hand-rolled version fails silently under `zsh`.

**Generated code is generated.** The OpenAPI spec, the route inventory, the
module contracts and the scenario coverage document are all produced by scripts
and gated in CI. Edit the source and re-run, never the output. `make quality`
checks all of it.

**Documentation is part of the change.** A new reader-facing document goes in
`docs/` and gets a row in its index. Do not paste coverage percentages or
benchmark numbers into prose — they go stale silently. Name the command that
produces them.

**Comments say why, and never carry production data.** A comment that restates
the code is one the next edit invalidates — make the code say it instead, with a
name or a smaller function. And nothing measured against production belongs in
the source: no traffic percentages, row or request counts, latencies, error
rates, costs, customer names or internal URLs. Keep the reasoning, drop the
figures. Configured limits, TTLs and API ceilings are contract and stay.
[CONTRIBUTING.md](CONTRIBUTING.md#code-and-comments) has the full rule.

**Write the type.** No `Any`, and no bare `dict`, `list` or `tuple`, in an
annotation. Both mean "not checked here", and they cost most at exactly the
place two pieces of code meet — a helper taking `service: Any` and calling six
methods on it has a type, it just isn't written down, so nothing notices when
one of those methods is renamed. Parameterise the container, or name the shape:
a `TypedDict`, a dataclass, a pydantic model, a `Protocol` for something you
only call methods on. `Any` is for data with no shape until it is validated — a
provider's JSON, an untyped third-party library — and then say so in a comment
and narrow it once the data is checked. `make architecture` ratchets the count
per module; it may fall, not rise.

## Before opening a pull request

```bash
make quality
```

`make quality` is Python only, all the way down. If you touched the frontend or
the TypeScript SDK, add `make quality-frontend` — eslint, `tsc`, the
design-system audit and the education anchors, the four gates CI runs that
`quality` cannot see. (`make check` is both, plus CodeQL.)

Then the checks for the component you touched, from the table in
`CONTRIBUTING.md`. The pull request template lists what the description needs —
including the exact commands you ran and what they printed.
