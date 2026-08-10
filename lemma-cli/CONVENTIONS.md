# Lemma CLI Conventions

Rules every command group must follow. When adding or changing commands, match these
patterns — consistency beats local convenience.

## Command shape

```
lemma <resource> <verb> [NAME] [flags]
```

Resource groups are registered with both singular and plural aliases (`pod`/`pods`).

## Verbs

| Verb | Meaning |
|------|---------|
| `list` | List resources (supports `--limit`). |
| `get NAME` | Fetch one resource. |
| `create` | Create a resource. For local config resources (servers) create is an upsert. |
| `update NAME` | Partially update a resource. |
| `delete NAME` | Delete a resource. Always confirms (see Destructive operations). |
| `select [NAME]` | Set the stored default (server/org/pod/conversation). With no NAME, opens an interactive picker. |

Domain verbs allowed in addition: `run` (execute something — functions, workflows,
agents, tools, queries), `chat`, `send`, `deploy`, `scaffold`, `enable`, `disable`,
`export`, `import`, `upload`, `download`, `search`. Do **not** introduce synonyms for
existing verbs (`execute`, `rm`, `add`, `remove` are banned — use `run`, `delete`,
`create`).

## Interactive selection

- Running a resource group with no subcommand (`lemma pods`, `lemma orgs`,
  `lemma servers`) opens the selection picker.
- `select` with the NAME omitted opens the same picker.
- Pickers use `cli_core/select.py:select_from_items` — arrow keys on a TTY, numbered
  prompt otherwise.

## Flags

| Flag | Use |
|------|-----|
| `--pod` | Pod override for pod-scoped commands. |
| `--org` | Organization override. |
| `--limit` | Max items for `list`-style commands. |
| `--data, -d` | Raw JSON payload input. |
| `--file, -f` | Read the JSON payload from a file (mutually exclusive with `--data`). |
| `--yes, -y` | Skip the confirmation prompt on destructive commands. |

The **global** `--json` / `--output json` flags (before the command) control output
format only. Never use `--json` as a per-command payload flag — that is what `--data`
is for.

## Destructive operations

Every `delete` (and any other irreversible command) must call
`cli_core/confirm.py:confirm_destructive(message, yes)`:

- Prompts `Delete <resource> <name>?` unless `--yes` was passed.
- In a non-interactive session (stdin is not a TTY) without `--yes`, it fails with
  exit code 1 instead of hanging or proceeding.

## Errors and exit codes

- Runtime errors (API failures, missing resources, invalid runtime payloads) go
  through `cli_core/state.py:fail(message)` → red message on stderr, exit code 1.
- `typer.BadParameter` is reserved for argument-parse-time validation only (exit 2).
- Exit codes: `0` success, `1` runtime error, `2` usage error.

## Output

- All command output goes through `cli_core/io.py:emit(state, payload)` so the global
  `--json`/`--output` flags work uniformly. Never `print()` results directly.

## State

- Stored config lives at `~/.lemma/config.json`: `active_server` + `servers.<name>`
  (each with `base_url`, `auth_url`, `token`, `auth`, `defaults{org_id, pod_id,
  conversation_id}`).
- The word for a stored backend connection is **server** (not context). The `env`
  server is synthesized from `LEMMA_*` env vars and is read-only.
- Resolution priority for org/pod/conversation: CLI flag → `LEMMA_*` env var →
  project `.lemma.<server>.env` → project `.lemma.env` → stored default.
- A project folder binds itself with **`.lemma.<server>.env`** files (dotenv syntax,
  committed, no secrets) keyed by the active server — so one repo can target a local
  pod and a cloud pod (server + pod change together). A base `.lemma.env` sets the
  folder's default `LEMMA_SERVER`; `.lemma.<server>.env` sets `LEMMA_POD_ID`. The CLI
  discovers the nearest anchor walking up from the cwd (ceiling: git repo root, else
  `$HOME`), resolves the active server, and applies the matching files' `LEMMA_*` keys
  before resolving state. Gitignored `.lemma.env.local` / `.lemma.<server>.env.local`
  override per-machine. Real process env always wins (a real `LEMMA_TOKEN` — e.g.
  a workspace sandbox — skips the files entirely). `init` writes these. See `SETUP.md`.
- `pods select` stores the pod **and** its org; `orgs select` stores the org and
  clears the pod (a pod belongs to one org).

## Help text

Every command has a one-line imperative docstring ending with a period
("Delete a function."). Group help describes the resource ("Agent surface commands
for Slack, Teams, ...").
