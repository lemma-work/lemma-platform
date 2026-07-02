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
