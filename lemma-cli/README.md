# Lemma CLI (`lemma-terminal`)

`lemma` is the command-line and terminal-UI for [Lemma](https://github.com/lemma-work/lemma-platform) —
build and operate **pods** (a team's tables, files, functions, agents, workflows, schedules,
connectors, surfaces, and apps) from your terminal, and drop Lemma's agent **skills** straight into
your coding agent so it can build pods for you.

## Install

```bash
uv tool install lemma-terminal
```

This installs the `lemma` command globally. (Don't have `uv`? See
[astral.sh/uv](https://astral.sh/uv).)

```bash
lemma --version
```

## Quickstart

```bash
lemma auth login            # authenticate against the default cloud server
lemma orgs select --save-default   # pick your organization
lemma pods select --save-default   # pick the pod to work in
lemma describe              # inventory the selected pod
```

From there, the command surface mirrors the resource model — `lemma <resource> <verb>`:

```bash
lemma tables list
lemma files ls /knowledge
lemma agent chat            # talk to the pod's default agent
lemma pod init my-pod       # scaffold a new pod bundle on disk to import
```

Working across several pods at once (e.g. a coding agent per repo)? `lemma app init`
and `lemma pods create --with-starter` write committed `.lemma.<server>.env` files
that bind the folder per server, so every command from that tree targets the right
pod — no per-shell exports, no global switching. The same repo drives local and cloud:

```bash
lemma pods describe                    # local pod  (from .lemma.local.env)
lemma --server lemma-cloud apps deploy # cloud pod  (from .lemma.lemma-cloud.env)
lemma config show                      # shows the resolved server, pod, and files
```

Add `--json` to any command for machine-readable output, and `--full` to expand folded fields.
See [SETUP.md](SETUP.md) for cloud/local server configuration, environment variables, project
`.lemma.<server>.env` files, and the Textual TUI (`lemma tui`).

## Install Lemma skills into your coding agent

Lemma ships agent **skills** (`SKILL.md` format) that teach a coding agent how to design, build, and
operate pods. `lemma skills` installs them into the coding agent you already use:

```bash
lemma skills list                       # what's bundled
lemma skills install                    # auto-detect Claude Code / Codex / OpenCode / Cursor and install
lemma skills install --target claude    # or pick one explicitly
lemma skills install --all-skills       # include workspace-runtime helpers too
```

`install` is an **upsert** — the CLI owns these skills, so an existing copy is overwritten to match
what this `lemma-terminal` bundles (re-run it after upgrading to refresh them; identical copies report
`unchanged`). By default it installs every namespaced `lemma-*` product skill: pod building and
operation, widgets, app design and QA, research, data analysis, artifact authoring, evaluations, and
pod-skill creation. `--all-skills` additionally installs the environment-specific `browser` and
`liteparse-documents` helpers. Targets and their locations:

| Target | Location (`--scope user`) | Tool |
|---|---|---|
| `claude` | `~/.claude/skills/` | Claude Code |
| `codex` | `~/.agents/skills/` | Codex CLI |
| `opencode` | `~/.config/opencode/skills/` | OpenCode |
| `cursor` | _project only_ → `.cursor/skills/` | Cursor (no global skills dir) |
| `agents` | `~/.agents/skills/` | shared (Codex + OpenCode) |
| `all` | all of the above | — |

Use `--scope project` to install into the current directory (`.claude/skills/`, `.agents/skills/`,
`.opencode/skills/`, `.cursor/skills/`), or `--dir PATH` for an arbitrary location. Cursor is
**project-scoped only** — run `lemma skills install --target cursor --scope project` inside the repo
you're working in. Then restart your coding agent and ask it to build a pod.

## How it fits together

- **`lemma-sdk`** — the Python client used by functions and automation.
- **`lemma-terminal`** (this package) — the human- and agent-facing CLI and TUI; pod-scoped
  workflows are first-class.
- **`lemma-stack`** — installs and manages the local Lemma stack (separate tool).

## License

Apache-2.0
