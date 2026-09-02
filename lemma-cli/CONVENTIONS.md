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
`export`, `import`, `upload`, `download`, `search`, `upsert`, `pull`, `pause`,
`resume`, `stop`, `approve`, `grant`, `validate`, `doctor`. Do **not** introduce
synonyms for existing verbs (`execute` and `rm` outside the `files` group are banned
— use `run` and `delete`).

Two carve-outs, both deliberate:

- **`lemma files` is POSIX-shaped on purpose.** `ls`, `cat`, `mv`, `rm`, `mkdir`,
  `stat`, `tree` read as a filesystem because that is what a pod's files are. The
  table above does not apply inside that group.
- **Permission sub-groups use `add`/`remove`**, because a permission is granted and
  revoked rather than created and deleted (`lemma agent permissions add`).

A handful of **top-level** commands take no resource: `init`, `chat`, `get`,
`describe`, `schema`, `doctor`, `feedback`, `version`, `update`. `lemma update`
upgrades the CLI itself; the `update NAME` row above is the resource verb
(`lemma agent update triage`), and the two never collide because the top-level form
takes no NAME. Every top-level command must also be listed in `_TOP_LEVEL_COMMANDS`
in `cli_core/app.py` or telemetry reports it as `None`
(`tests/test_update_check.py` pins this).

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

Every command that is **irreversible or externally visible** must call
`cli_core/confirm.py:confirm_destructive(message, yes)` — not only `delete`. That
includes writing into another tool's config (`skills uninstall`) and authorising
queued agent actions in bulk (`conversation approve` with no id), both of which
have a wider blast radius than most deletes.

- Prompts `Delete <resource> <name>?` unless `--yes` was passed.
- In a non-interactive session (stdin is not a TTY) without `--yes`, it fails with
  exit code 1 instead of hanging or proceeding.
- A bulk form must print what it is about to act on **to stderr** first, so the
  prompt is a decision rather than a guess.
- Where `install`/`uninstall` are paired, both take `--yes` and `--dry-run`.

## Errors and exit codes

- Runtime errors (API failures, missing resources, invalid runtime payloads) go
  through `cli_core/state.py:fail(message)` → red message on stderr, exit code 1.
- `typer.BadParameter` is reserved for argument-parse-time validation only (exit 2).
- Exit codes: `0` success, `1` runtime error, `2` usage error.
- **Diagnostics on stderr, results on stdout.** `state.py` defines `console`
  (stdout, for results) and `err_console` (stderr, for warnings, progress, previews
  and every failure). `--output json` promises a parseable stdout *including on
  failure*: one advisory in the wrong stream breaks every `| jq` and every agent
  driving the CLI. A bare `print()` needs a `# noqa: T201` naming why.

## Output

- All command output goes through `cli_core/io.py:emit(state, payload)` so the global
  `--json`/`--output` flags work uniformly. Never `print()` results directly.
- Every hint must name a command the user can actually run. A hint that cannot be
  acted on is a bug, not a nicety.
- Anything a loader computes for the user's safety has to be rendered somewhere.
  `project_env.load_project_env` detects a token committed to a project file; if
  nothing showed it, the detection would not exist.

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
