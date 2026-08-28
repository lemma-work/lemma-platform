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
- **[docs/product/](docs/product/README.md)** — what the product promises, in
  product language, with stable ids. **It is normative:** it says what the
  product is *meant* to do, not what the code currently does.
- **[docs/testing.md](docs/testing.md)** — the three suites, which one your
  change needs, and what gates what.

## The five that get broken most

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

## Before opening a pull request

```bash
make quality
```

Then the checks for the component you touched, from the table in
`CONTRIBUTING.md`. The pull request template lists what the description needs —
including the exact commands you ran and what they printed.
