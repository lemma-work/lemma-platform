# Engineering standards

The rules a change here is held to, written so that a person or a coding agent
can follow them without having read the rest of the codebase first.

[CONTRIBUTING.md](../../CONTRIBUTING.md) says what a pull request needs.
[AGENTS.md](../../AGENTS.md) is the map. This is the standard the code itself is
held to.

| Document | Covers |
|---|---|
| [design.md](design.md) | Module boundaries, ports and adapters, services, composition, shapes, events |
| [types.md](types.md) | Typed interfaces, `Any`, named shapes, enums, the type-checker path |
| [tests.md](tests.md) | What a good test looks like in any of the three suites |

Three more documents are part of the same standard and are **not** restated here,
because a rule with two homes has two versions within a year:

| Document | Covers |
|---|---|
| [lemma-backend/docs/development.md](../../lemma-backend/docs/development.md) | DB sessions, caching, errors and logging, concurrency and external I/O, authorization, secrets — each with the canonical example to copy |
| [docs/testing.md](../testing.md) | Which of the three suites a change needs, and what gates a merge |
| [lemma-cli/CONVENTIONS.md](../../lemma-cli/CONVENTIONS.md) | Command shape, verbs, flags, output, exit codes |

---

## How to read a rule

Every rule has an id (`DES-04`, `TYP-09`, `TST-05`) so a review comment, a lint
message or a commit can cite one thing and mean one thing. Each states the check
that enforces it and what that check reports today:

**Hard** — the count is zero and any new instance fails the build. Cite the rule
and fix the code; there is no baseline to add to.

**Ratchet** — the count is not zero. Existing violations are tolerated; the number
may fall and never rise. This is how nearly every rule here starts, because the
debt is real and rewriting it at once is not a plan. A ratchet is not a
suggestion: adding to the count fails the build exactly as a hard rule does.

**Review** — no automated check exists yet. The rule still holds; the pull request
is where it is enforced. A review-only rule should carry a note about what would
make it checkable.

### Which linter rules are on

`lemma-backend/pyproject.toml` selects ruff families explicitly, and a family is
added to that list **only once it is at zero** — so ruff never carries a
baseline, and a finding from it is always a real failure rather than a number to
compare. The families deliberately left out are listed there with today's count
and the reason, so choosing the next one to adopt does not need a re-measurement.
`lemma-cli` and `lemma-python` carry the same list, minus what does not apply.

The rules with large existing counts — `Any`, broad excepts, complexity, file
size — are ratcheted by the scripts in `lemma-backend/scripts/check_*.py`
instead, which do carry baselines and can therefore tolerate the debt while
forbidding more of it.

The counts are measurements, not targets, and they go stale. Each rule ships the
command that produces its number — run it rather than trusting the text.

## Before opening a pull request

```bash
make quality
```

Runs in about 35 seconds and is the whole Python gate: formatting and ruff across
the backend, the CLI, both SDKs, the stack, the bundle and the scenario suite;
async safety; DB connection scope; I/O hygiene; swallowed errors; import budget;
the critical typecheck; the architecture ratchet; the logging event catalog;
OpenAPI freshness; module contracts; the test census; and scenario traceability.

CI runs this exact command — one job, one list. It is deliberately not
path-filtered, because a skipped required check counts as a satisfied one.

Touched the frontend or the TypeScript SDK? Add `make quality-frontend`.
`make check` is both plus CodeQL. Then run the component's own checks from the
table in [CONTRIBUTING.md](../../CONTRIBUTING.md#find-the-right-component).

## For coding agents

If you are an agent making a change here, four things matter more than the rest:

1. **Run backend Python through `uv`, from `lemma-backend/`.** Never bare
   `python3` or `pytest`. The backend is 3.14, and `except A, B:` is valid
   [PEP 758](https://peps.python.org/pep-0758/) syntax that this codebase uses —
   not a bug to fix.
2. **Write the type** ([types.md](types.md)). This is the rule most worth
   following literally: no `Any`, no bare `dict`/`list`/`tuple` in an annotation,
   and a `Protocol` for anything you only call methods on.
3. **Match the surrounding code.** Comment density, naming, and idiom. A change
   that reads as though a different person wrote it costs a reviewer more than
   the change itself.
4. **`make quality` before you claim you are done**, and report what it printed.

The specification in [docs/product/](../product/README.md) is normative: it says
what the product is *meant* to do. If the code disagrees with it, the code is
what changes.

## Adding a rule

A rule earns its place by naming a failure that has actually happened here, and
by being checkable. Concretely:

1. **State the failure, not the preference.** "A service that builds its own HTTP
   client is untestable and usually unbounded" is a rule. "Prefer dependency
   injection" is a mood.
2. **Measure it.** Write the grep, the ruff selector, or the AST pass, and record
   what it returns today. That number is the ratchet's start.
3. **Wire it into a gate**, or mark it `Review` and say what would make it
   checkable.
4. **Give it one home.** If it belongs in `development.md`, put it there and link
   it from here.

The gates carry their reasoning: `scripts/check_architecture.py:63-77` explains
*why* a bare `dict` counts as an untyped escape, and the `typecheck-critical`
list names the concrete outage each entry's absence caused. Every new gate should
read the same way — a check whose message does not say why it exists is one that
gets baselined the first time it is inconvenient.
