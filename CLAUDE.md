# Working in this repository

Read **[AGENTS.md](AGENTS.md)** first — it is the map, and it points at the rules
in [CONTRIBUTING.md](CONTRIBUTING.md). This file exists only so the one rule that
is wrong by default arrives without anyone having to go looking for it.

## Run Python through `uv`

From `lemma-backend/`:

```bash
uv run python ...
uv run pytest ...
```

Never bare `python3` or `pytest`. The backend is **Python 3.14**; a bare
`python3` on macOS is Xcode's 3.9, and the root `.python-version` pins the
version for `uv`, not for your `PATH`.

The failure this prevents is not a missing import. 3.14 accepts
[PEP 758](https://peps.python.org/pep-0758/) unparenthesised exception tuples —
`except TypeError, ValueError:` is a two-type handler, and this codebase uses it
— so an older interpreter reports a `SyntaxError` in correct, shipping code. An
audit did exactly that and reported a working module as broken.

Everything else — which checks to run, what a pull request needs, how the three
test suites differ — lives in `AGENTS.md` and `CONTRIBUTING.md`. Kept there
rather than copied here so the two cannot drift.
