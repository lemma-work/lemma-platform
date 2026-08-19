"""Keep the repo-tooling tests and the scenario suite apart.

`tests/` holds a handful of tests for this repository's own scripts, and it also
holds `tests/scenarios/` — a separate `uv` project with its own dependencies,
its own `pyproject.toml`, and its own way of being run (`make scenarios`).

Collecting them together does not work and should not: `pytest tests/` from the
repo root picks up the scenario suite, fails to import `pytest_asyncio`, and
dies during collection before a single tooling test runs. That is what the
`Dev workflow` CI job does, and the failure cascades — CI goes red, and
`e2e.yml` never fires at all, because it waits on CI succeeding.

Ignoring it here rather than in the workflow means the same command works for
whoever runs it locally.
"""

collect_ignore = ["scenarios"]
