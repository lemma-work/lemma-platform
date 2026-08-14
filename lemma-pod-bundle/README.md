# lemma-pod-bundle

Shared pod bundle format vocabulary for the Lemma CLI and backend.

A *pod bundle* is the on-disk directory format the Lemma CLI exports pods to
and imports pods from (`pod.json` manifest plus per-resource directories:
`tables/`, `functions/`, `agents/`, `workflows/`, `schedules/`, `surfaces/`,
`apps/`, `files/`). This package holds the pure, dependency-free pieces of that
format so the CLI and the backend agree on it without either depending on the
other:

- `layout` — format constants and manifest/file-layout helpers
- `jsonc` — JSONC parsing (comments + trailing commas) for bundle files
- `diff` — table column diffing and foreign-key dependency ordering
- `portability` — `${name}` portable-variable extraction and stripping
- `normalize` — per-resource payload normalization and validation
- `archive` — deterministic zip packing and safe extraction of bundle dirs

Stdlib only; no runtime dependencies.

## How it ships

This package is not published to PyPI. Inside the repo, both consumers resolve
it from this directory through `[tool.uv.sources]`. For distribution,
`lemma-cli/setup.py` vendors `lemma_pod_bundle/` into the `lemma-terminal`
wheel at build time, since an installed CLI has no other way to get it.

## License

Apache-2.0 — see [LICENSE](LICENSE).

This is the permissive side of the repo's [licensing
boundary](../ARCHITECTURE.md#licensing-boundary), and deliberately so: the
Apache-2.0 CLI and the AGPLv3 backend both depend on this package, and it
travels inside an Apache-2.0 wheel. Keep it stdlib-only and do not import
anything AGPL-licensed into it.
