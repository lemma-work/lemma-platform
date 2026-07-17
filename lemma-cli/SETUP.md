# Lemma CLI Setup Guide

`lemma-terminal` is the command-line and terminal UI app for Lemma. It talks to
either Lemma Cloud or a local Lemma stack through named servers.

## Install

Keep a **single global install** so `lemma` always resolves to one version. Use
`uv tool` (not a project venv or `pip install` into an arbitrary environment) —
that is what puts `lemma` on your PATH once:

```bash
uv tool install lemma-terminal
```

For local development from this repository (editable, picks up source changes):

```bash
uv tool install --force --editable lemma-cli
```

> **After the SDK schema changes** (regenerating `lemma-python`), re-run the
> `--force` install so the bundled `lemma-sdk` is rebuilt. `lemma doctor` flags
> when the installed SDK has drifted from the server it is talking to — the exact
> skew that once shipped a stale message model under an unchanged version.

Check the install, versions, and health:

```bash
lemma --help
lemma --version          # CLI + SDK + API schema versions
lemma doctor             # client/server skew + duplicate-install check
lemma servers list
```

## Cloud Setup

The CLI ships with a single server, **`lemma-cloud`**, already active:

- API: `https://api.lemma.work`
- Auth: `https://lemma.work/auth`

So a fresh install just logs in:

```bash
lemma auth login
```

List organizations you can access:

```bash
lemma orgs list
```

Select defaults for commands that work inside a pod:

```bash
lemma orgs select --save-default
lemma pods list
lemma pods select --save-default
```

Most pod workflows then use the selected org and pod automatically:

```bash
lemma agents list
lemma files list /pod
lemma tables list
lemma chat
```

Use `--json` when an agent or script needs raw structured output:

```bash
lemma --json pods list
```

## Servers

Servers are independent CLI states. Each server stores its API URL, auth URL,
token, and default org/pod/conversation values.

```bash
lemma servers list
lemma servers show
lemma servers select cloud
lemma servers create staging --base-url https://api.example.com --auth-url https://example.com/auth
```

## Environment Variables

Environment variables continue to work for humans, scripts, and agents:

- `LEMMA_SERVER`: active server name.
- `LEMMA_BASE_URL`: API URL override.
- `LEMMA_AUTH_URL`: auth URL override.
- `LEMMA_TOKEN`: bearer token override.
- `LEMMA_ORG_ID`: org override.
- `LEMMA_POD_ID`: pod override.
- `LEMMA_CONVERSATION_ID`: conversation override.

Command-line flags take precedence over environment variables.

## Project folders (`.lemma.<server>.env`)

Working across several pods — e.g. a coding agent (Claude Code, Codex) in a
different repo per pod — no longer needs per-shell `export`s or mutating the global
config. A project's **server and pod change together** (local stack vs. cloud), so
the CLI reads a small family of files keyed by the active server (Vite's
`.env.<mode>` model). `lemma app init` / `lemma pods create --with-starter` write
them for you; you can also edit by hand:

```sh
# .lemma.env — base. Commit. Optionally sets the folder's default server.
LEMMA_SERVER=local

# .lemma.local.env — binding for the `local` server. Commit. NO secrets.
LEMMA_POD_ID=pod_local_abc
# LEMMA_ORG_ID=org_...        # optional — resolved from the pod if omitted

# .lemma.lemma-cloud.env — binding for the `lemma-cloud` (cloud) server. Commit.
LEMMA_POD_ID=pod_cloud_xyz
```

Now the same repo drives both targets:

```bash
lemma pods describe                    # uses .lemma.local.env (folder default server)
lemma --server lemma-cloud apps deploy # uses .lemma.lemma-cloud.env
```

- The CLI loads the **nearest** anchor (`.lemma.env` or any `.lemma.<server>.env`)
  walking up from the cwd (ceiling: the git repo root, else `$HOME`), so it works
  from any subdirectory. The active server is resolved exactly as elsewhere: `--server`
  → `LEMMA_SERVER` → the base file's `LEMMA_SERVER` → your config's `active_server`.
- Personal per-machine overrides live in gitignored `.lemma.env.local` /
  `.lemma.<server>.env.local`. Precedence (low→high): `.lemma.env` <
  `.lemma.env.local` < `.lemma.<server>.env` < `.lemma.<server>.env.local` < real env
  < `--flag`.
- Only `LEMMA_*` keys are read; a real shell/agent env var always wins over the files.
- Bind an unbound server with `lemma app init` / `lemma pods create --with-starter`;
  otherwise a command that needs a pod fails with a clear
  `No pod bound for server '<server>'` hint.
- **Don't commit tokens.** Auth comes from your stored login (`lemma auth login`);
  `LEMMA_TOKEN` is an agentbox concept. A real `LEMMA_TOKEN` in the environment makes
  the CLI ignore the project files entirely.
- `lemma config show` reports the resolved server and which files were applied.

## Terminal UI

Open the TUI:

```bash
lemma tui
```

The TUI shows the active server, org, pod, and agent. It includes resource
views for servers, organizations, pods, and pod-scoped resources. You can switch
server/org/pod from the resource views or with chat slash commands:

```text
/server cloud
/org <org-id-or-slug>
/pod <pod-id-or-slug>
/refresh
/quit
```

`Ctrl-C` and `q` exit the TUI.

## Local Stack Setup

Installing and managing a local Lemma stack is handled by the separate
`lemma-stack` tool, not the CLI. Install and start it with:

```bash
curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/v0.5.4/install.sh | bash
```

`lemma-stack install` registers the stack as the CLI server named `local`
(API `http://localhost:8711`, auth `http://localhost:3711/auth`), so afterwards:

```bash
lemma servers select local
lemma auth login
```

Manage the stack with `lemma-stack start|stop|status|logs|config|uninstall`.
See `lemma-stack --help` and the `lemma-stack/` package for details.

## Common Workflow

Cloud (the shipped default — nothing to add):

```bash
lemma auth login
lemma orgs select --save-default
lemma pods select --save-default
lemma tui
```

Local (after `lemma-stack install`):

```bash
lemma servers select local
lemma auth login
lemma orgs select --save-default
lemma pods select --save-default
lemma tui
```
