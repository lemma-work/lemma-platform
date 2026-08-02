# Releasing `lemma-terminal` to PyPI

`lemma-terminal` is the PyPI distribution for `lemma-cli` (the `lemma` command).
It is published from this monorepo and follows the same mono-version convention
as `lemma-sdk` / `lemma-typescript`: **one GitHub release `vX.Y.Z` publishes every
package at that version.**

## Release `lemma-sdk` and `lemma-terminal` together

The CLI uses `lemma-sdk` endpoints, so it pins `lemma-sdk>=<this CLI's version>`
in [`pyproject.toml`](pyproject.toml). Two rules follow:

- **Always release the matching `lemma-sdk` first** (same version), so the CLI's
  floor resolves. A floor pointing at an unpublished SDK breaks `uv tool install`.
- **Keep three versions in lockstep at `X.Y.Z`:** `lemma_cli/__init__.py`
  `__version__`, the `lemma-sdk>=X.Y.Z` floor here, and
  [`../lemma-python/pyproject.toml`](../lemma-python/pyproject.toml) `version`.
  The last one matters for CI: the `python-sdk` job does `pip install -e
  ./lemma-python && pip install -e ./lemma-cli`, and pip (unlike uv) ignores
  `[tool.uv.sources]`, so the editable SDK's version must satisfy the CLI floor.

The release workflow rewrites `__version__` **and** the `lemma-sdk>=` floor from
the tag automatically; the SDK release workflow sets `lemma-python`'s version
from the same tag. Bumping the floor forces an already-installed older SDK to
upgrade (a `>=` floor that's already satisfied won't).

## The one thing that breaks releases

The CLI **ships a copy of the agent skills** inside the wheel (so `lemma skills
install` works without a repo checkout). The canonical source is the repo-root
[`lemma-skills/`](../lemma-skills); it is **vendored into `lemma_cli/skills/` at
build time** and that directory is **gitignored** — a clean checkout has nothing
there.

> ⚠️ `lemma-terminal` 0.4.1 shipped with the `lemma skills` command but **no
> skills**, because it was built with a bare `python -m build` that never
> vendored them.

Two safeguards now make this impossible to repeat:

1. [`setup.py`](setup.py) vendors `../lemma-skills` into `lemma_cli/skills/` on
   every `sdist`/`build_py`, and **fails the build** if neither the source nor a
   vendored copy is present.
2. The release workflow runs the sync explicitly and asserts the wheel contains
   `SKILL.md` files before publishing.

You must build **from the monorepo** (so `../lemma-skills` exists). Never publish
a wheel built outside it.

## Recommended: release via GitHub (CI)

1. Bump the version — it is single-sourced in
   [`lemma_cli/__init__.py`](lemma_cli/__init__.py) (`__version__`); pyproject reads
   it dynamically. The release workflow also sets it from the tag, so this is
   mostly for local correctness.
2. Push a tag and publish a GitHub Release named `vX.Y.Z`.
3. The [`Release Lemma Terminal CLI`](../.github/workflows/release-lemma-terminal.yml)
   workflow then: sets the version from the tag → vendors skills → `python -m
   build` → asserts the wheel has skills → `twine check` → `twine upload`.

Requires the `PYPI_API_TOKEN` repository secret (shared with the other release
workflows).

## Manual release

From the **repo root**:

```bash
# 1. Bump the version
$EDITOR lemma-cli/lemma_cli/__init__.py        # __version__ = "X.Y.Z"

# 2. Build (setup.py vendors the canonical repo-root skills automatically)
cd lemma-cli && uv build && cd ..

# 3. VERIFY both commands print the same count (currently 12)
find lemma-skills -mindepth 2 -maxdepth 2 -name SKILL.md | wc -l
unzip -l lemma-cli/dist/lemma_terminal-*.whl | grep -c 'SKILL.md$'

# 4. Check metadata + long description
uvx twine check lemma-cli/dist/*

# 5. Publish
uvx twine upload lemma-cli/dist/*
```

## Verify a published release

In a throwaway environment, confirm the skills actually shipped:

```bash
uv tool install --force lemma-terminal==X.Y.Z
lemma skills list            # must list lemma-builder, lemma-user, lemma-widget, ...
```

Or inspect the wheel without installing:

```bash
pip download --no-deps lemma-terminal==X.Y.Z -d /tmp/lt && \
  unzip -l /tmp/lt/lemma_terminal-*.whl | grep SKILL.md
```

If `SKILL.md` is absent, the release is broken — bump the patch version and
re-release (PyPI versions are immutable).
