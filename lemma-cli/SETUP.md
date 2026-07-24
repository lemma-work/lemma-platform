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

Install the signed Lemma Desktop online package, choose **Local**,
and let the first setup finish once. Desktop owns the private VZ/WSL2 runtime;
Docker, Podman, and raw `localhost` URLs are not part of the managed path.

The optional `lemma-stack` control CLI can start and manage that same install
while the Desktop window is closed. On macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.sh |
  bash -s -- --cli-only
```

On Windows PowerShell:

```powershell
$env:LEMMA_STACK_CLI_ONLY = "1"
irm https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.ps1 | iex
Remove-Item Env:LEMMA_STACK_CLI_ONLY
```

CLI-only bootstrap registers the server named `local`. It discovers the current
API and auth origins from Desktop's app-owned locald state; the persistent
high ports are selected during installation and must not be hardcoded.

Then select it and authenticate:

```bash
lemma servers select local
lemma auth login
```

Use `lemma-stack start|stop|restart|status|doctor|logs|config` for managed
operations. `lemma-stack uninstall`, `db`, and `redis` are container-oriented
commands for the explicit external-runtime compatibility path, not Desktop.
See the [local installation guide](../docs/installation.md) for package
selection, first-run setup, Control Center, configuration, and repair.

## Common Workflow

Cloud (the shipped default — nothing to add):

```bash
lemma auth login
lemma orgs select --save-default
lemma pods select --save-default
lemma tui
```

Local (after Desktop setup and CLI-only registration):

```bash
lemma servers select local
lemma auth login
lemma orgs select --save-default
lemma pods select --save-default
lemma tui
```
