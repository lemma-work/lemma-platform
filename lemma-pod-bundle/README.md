# lemma-pod-bundle

Shared pod bundle format vocabulary for the Lemma CLI and backend.

A *pod bundle* is the on-disk directory format the Lemma CLI exports pods to
and imports pods from (`pod.json` manifest plus per-resource directories:
`tables/`, `deciders/`, `functions/`, `agents/`, `workflows/`, `schedules/`,
`surfaces/`, `apps/`, `files/`). This package holds the pure, dependency-free
pieces of that format so the CLI and the backend agree on it without either
depending on the other:

- `layout` — format constants and manifest/file-layout helpers
- `jsonc` — JSONC parsing (comments + trailing commas) for bundle files
- `diff` — table column diffing and foreign-key dependency ordering
- `portability` — `${name}` portable-variable extraction and stripping
- `normalize` — per-resource payload normalization and validation
- `archive` — deterministic zip packing and safe extraction of bundle dirs

Stdlib only; no runtime dependencies.

## Deciders

A decider (a pod's named, versioned closed-set judgement) is one file in a
folder of its own name, like every other resource:
`deciders/<name>/<name>.json`, holding the decider's `name` and its current
`definition` and nothing else.

```json
{
  "name": "email-triage",
  "definition": {
    "description": "What Kit does with each new email.",
    "input": {"fields": ["from", "subject", "labels"], "max_chars": 4000},
    "questions": {
      "action": {
        "type": "choice",
        "prompt": "What should Kit do with this email?",
        "options": {
          "act": {"description": "A customer is waiting.", "examples": []},
          "ignore": {"description": "Newsletters and promotions.", "examples": []}
        },
        "fallback": "ignore"
      }
    },
    "rules": [
      {"when": "contains(labels, 'PROMOTIONS')", "field": "text", "answer": {"action": "ignore"}}
    ],
    "policy": {"lane": "ambient", "escalate_to_model": true, "abstain_below": 0.6,
               "yes_no_band": [0.35, 0.65], "rules_only": {}, "require_confidence": {}}
  }
}
```

- **What travels is the definition, never what the decider learned.** Its
  examples are people's answers about their own data, and its decisions are
  records of what it saw; both stay in the pod. `_normalize_decider_payload`
  keeps the two keys above rather than stripping others, so a field the API
  adds later cannot leak by default. `examples` inside an option are part of
  the definition, written with it, and do travel. The version stays behind
  too: the importing pod keeps its own history.
- **Unset fields are left out**, so the CLI's export (from the API, which
  writes them as null) and the backend's (from its store, which omits them)
  write the same bytes.
- **Import creates the decider, or saves the definition as its next version**,
  keeping the old ones. A decider that already has exactly this definition is
  left alone, so importing an unchanged bundle again saves no version.
- **Deciders import straight after tables**, before functions, agents,
  workflows and schedules: a workflow's DECISION step, a schedule, and an
  agent's `decider:<name>:execute` grant can all name one.
- A file left loose at `deciders/<name>.json` is refused on import rather than
  skipped.

`FORMAT_VERSION` did not move for this. It marks a change in how an importer
must read content it already knows (3 is when account variables began to require
their connector). Nothing gates on it, and every importer reads only the
directories it knows, so a bump would not stop an older CLI or server from
importing a bundle without its deciders -- and without saying so.

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
