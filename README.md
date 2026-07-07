<div align="center">

<img src="docs/Assets/Banner/banner.jpg" alt="Lemma — where humans and AI agents work as one team" width="100%">

**Tables, workflows, approvals, and the app your team runs on. One pod — not a pile of glue code.**

![License](https://img.shields.io/github/license/lemma-work/lemma-platform)
![Release](https://img.shields.io/github/v/release/lemma-work/lemma-platform)
![Build](https://img.shields.io/github/actions/workflow/status/lemma-work/lemma-platform/ci.yml)
[![Docs](https://img.shields.io/badge/docs-lemma.work-blue)](https://lemma.work)

[Quickstart](#quickstart) · [Inside a pod](#inside-a-pod) · [Why Lemma](#chat-is-not-where-work-lives) · [Surfaces](#use-it-from-anywhere) · [Coding agents](#the-back-layer-for-your-coding-agents) · [Docs](https://lemma.work)

Website → **[lemma.work](https://lemma.work)**

</div>

---

## Works with

<div align="center">

<table>
  <tr>
    <td align="center"><strong>Works&nbsp;with</strong></td>
    <td align="center"><img src="docs/Assets/Logos/claude.svg" height="40" alt="Claude Code"><br><sub>Claude Code</sub></td>
    <td align="center"><img src="docs/Assets/Logos/codex.svg" height="40" alt="Codex"><br><sub>Codex</sub></td>
    <td align="center"><img src="docs/Assets/Logos/opencode-logo-light.svg" height="40" alt="OpenCode"><br><sub>OpenCode</sub></td>
    <td align="center"><img src="docs/Assets/Logos/cursor.svg" height="40" alt="Cursor"><br><sub>Cursor</sub></td>
    <td align="center"><img src="docs/Assets/Logos/bash.svg" height="40" alt="Bash"><br><sub>Bash</sub></td>
    <td align="center"><img src="docs/Assets/Logos/http.svg" height="40" alt="HTTP"><br><sub>HTTP</sub></td>
  </tr>
</table>

<em>Anything that can run a CLI can work a pod.</em>

</div>

---

## Chat is not where work lives

AI can answer questions, draft replies, and call tools. But a chat thread is not a place where work can live.

Real work runs for days or weeks. It has owners. It has state that many people and agents need to read and write. It has steps that must wait for a human decision, and actions an agent should never take alone. Today that work is either trapped in chat scrollbacks, or stitched together from a database, a workflow tool, an auth layer, a UI builder, and glue code.

Lemma is the missing piece: **a shared workspace built for both kinds of participants from day one.**

- **Humans** get apps, approval queues, and the chat tools they already use.
- **Agents** get a CLI and SDKs that read and write the same state natively.
- **The pod** holds the tables, files, workflows, permissions, and approvals that make it one system instead of a pile of connectors.

The breakout AI products already point this way. Gamma turns a prompt into an editable deck, not a transcript. Cursor lands its work as diffs in your editor. Granola turns a meeting into structured notes. The shape is the same everywhere — an agent works in the background, and structured output lands in a purpose-built UI. Lemma is that shape for *your* work: an agent's output is a row in your table, a task in your queue, a draft waiting for your approval.

## Local-first, no lock-in

- **The Mac app.** Download Lemma, open it, and choose at first run: keep everything on your machine, or connect to the **lemma.work** cloud. Same product either way, and you can change your mind later.
- **Your machine.** The full stack runs self-contained on your laptop — one command on any OS with Docker or Podman. Your data never leaves unless you wire it somewhere.
- **Our cloud, when you want it.** [lemma.work](https://lemma.work) runs the same open-source stack — for when you want your pod reachable by teammates and surfaces without hosting anything.
- **Your subscription.** Already pay for Claude or ChatGPT? Lemma agents can run through your local **Claude Code or Codex logins** — no separate API key, no per-token bill.
- **Your keys, your models.** Or bring any **Anthropic-compatible or OpenAI-compatible** key or endpoint — a cloud provider, a self-hosted gateway, or a local model behind an OpenAI-compatible server. Runtime profiles are configured per pod, so different agents can run on different models.
- **Your code.** Core is [AGPLv3](LICENSE); SDKs and CLI are [Apache-2.0](LICENSES/Apache-2.0.txt).

## Quickstart

**Easiest — use it with the coding agent you already have.** Sign up at **[lemma.work/start](https://lemma.work/start)**, install the CLI, and drop Lemma's skills into your agent:

```bash
uv tool install lemma-terminal
lemma skills install          # auto-detects Claude Code / Codex / OpenCode / Cursor
```

Now your agent can build and operate pods. Authenticate, create one, and start working:

```bash
lemma auth login
lemma pod create my-team --with-starter   # scaffolds a working starter (table + agent) and imports it
lemma chat "what can you do in this pod?"
```

To run your coding agent *inside* Lemma — picking up tasks from a shared queue, streamed back through the pod — start the daemon:

```bash
lemma daemon start            # serves pod-assigned runs via your local Claude Code / Codex / OpenCode
```

**Run it locally — two ways.**

- **The Mac app.** Download Lemma, open it, and pick local or cloud at first run.
  <!-- TODO(launch): Mac app download link + a one-line note on auto-updates. -->
- **From source / raw GitHub.** One command brings the full stack up (Docker or Podman; the installer can set up Podman for you):

**macOS / Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.sh | bash
```

**Windows** (PowerShell, Docker Desktop required):
```powershell
iwr https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.ps1 | iex
```

This installs the `lemma-stack` tool and runs the app at `http://127-0-0-1.sslip.io:3711` and the API at `http://127-0-0-1.sslip.io:8711` (docs at `/scalar`). Use that `127-0-0-1.sslip.io` host — it resolves to `127.0.0.1`, but sign-in is scoped to it, so `localhost` / `127.0.0.1` won't authenticate. Manage it with `lemma-stack start|stop|status|logs|config|uninstall`. Point the CLI at it:

```bash
lemma servers select local
lemma auth login
```

Set model keys and backend env (stored under `[backend.env]` in `~/.lemma/local/config.toml`), then restart:

```bash
# Required — pick a model provider (set the type + key together):
lemma-stack config set LEMMA_DEFAULT_MODEL_TYPE anthropic_compat
lemma-stack config set LEMMA_ANTHROPIC_API_KEY sk-ant-...
# (or openai_compat + LEMMA_OPENAI_API_KEY / _BASE_URL / _DEFAULT_MODEL / _MODEL_NAMES —
#  works with OpenAI, Fireworks, a local server, any OpenAI-compatible endpoint)
# Recommended — enables the app connectors / integrations:
lemma-stack config set COMPOSIO_API_KEY <key>
lemma-stack restart
```

See [`docs/installation.md`](docs/installation.md#configure) for the full per-provider setup.

See [`docs/installation.md`](docs/installation.md) for the full env list and setup guide.

## Start from a pod, not a blank page

A pod is a directory of plain files — tables, agents, workflows, permissions, apps, all of it. That makes pods portable: export one, edit it, import it back. Or import one somebody else built.

```bash
lemma pod export ./my-team       # the whole system, as files
lemma pod import ./my-team       # ship it back — or to another machine
```

## Inside a pod

Everything in Lemma lives in a **pod** — a self-contained workspace for one team or process.

| Primitive | What it gives you |
|---|---|
| **Tables** | Typed, queryable business data with row-level security. Leads, tickets, tasks, approvals — readable by agents, owned by the pod. |
| **Files** | Markdown memory for everything structure can't capture — preferences, playbooks, voice guides, notes. Full-text searchable, permission-scoped, read and written by agents alongside the tables. |
| **Agents** | LLM workers with a role, tool grants, and scoped access to specific tables, files, and connectors — never vague access to everything. |
| **Workflows** | Graphs that mix agents, functions, decisions, loops, waits, and **human approval steps**. Triggered by schedules, webhooks, table events, chat, or the API. |
| **Functions** | Deterministic logic alongside the agents — validators, transitions, actions. Not everything should be LLM reasoning. |
| **Permissions** | Roles for people *and* agents: pod-level roles, table grants, resource visibility, delegation tokens. |
| **Approvals** | Workflow steps that pause, route to a specific person, and resume on their decision — in the app or in Slack. |
| **Apps** | The operator UI your team works from, deployed at a URL, built on the same pod APIs — a single-file HTML page (no build) or a full React app. |
| **Surfaces** | Slack, Microsoft Teams, Gmail, Outlook, Telegram, and WhatsApp — wired to pod agents with identity resolution and conversation linking. |

## Use it from anywhere

Chat is a door, not the building.

A teammate approves a refund **in Slack**. A field update arrives as a **WhatsApp** voice note and lands as a structured record. An agent drafts a customer reply **in Gmail** and waits for a human before sending. The conversation is the surface — underneath, all of it reads and writes the same tables, runs through the same workflows, and respects the same permissions.

Supported today: **Slack, Microsoft Teams, Gmail, Outlook, Telegram, WhatsApp** — each with webhook ingress, identity resolution, and agent-initiated actions. Telegram long-polling and Slack Socket Mode are built in, so local setups work without a public webhook URL.

<div align="center">

<table>
  <tr>
    <td align="center"><strong>Surfaces</strong></td>
    <td align="center"><img src="docs/Assets/Logos/slack.svg" height="40" alt="Slack"><br><sub>Slack</sub></td>
    <td align="center"><img src="docs/Assets/Logos/microsoft-teams.svg" height="40" alt="Microsoft Teams"><br><sub>Teams</sub></td>
    <td align="center"><img src="docs/Assets/Logos/Gmail.svg" height="40" alt="Gmail"><br><sub>Gmail</sub></td>
    <td align="center"><img src="docs/Assets/Logos/outlook.svg" height="40" alt="Outlook"><br><sub>Outlook</sub></td>
    <td align="center"><img src="docs/Assets/Logos/telegram.svg" height="40" alt="Telegram"><br><sub>Telegram</sub></td>
    <td align="center"><img src="docs/Assets/Logos/WhatsApp.svg" height="40" alt="WhatsApp"><br><sub>WhatsApp</sub></td>
  </tr>
</table>

<em>Wherever your team already works, the pod shows up.</em>

</div>

This isn't only for teams. A pod of one human and a few agents — with WhatsApp as the front door and tables as the memory — is a personal assistant that actually keeps state, asks before it acts, and picks up tomorrow where it left off today.

## The back layer for your coding agents

You don't have to make Lemma your front door. It can simply be **where your agents' work lands.**

**Install Lemma's skills into the agent you already use** — Claude Code, Codex, OpenCode, or Cursor — and it can build and operate pods directly:

```bash
lemma skills install             # auto-detects Claude Code / Codex / OpenCode / Cursor
lemma skills install --target claude --all-skills   # or pick a target and include extras
```

Skills ship in [`lemma-skills/`](lemma-skills/). Restart your coding agent after installing, then ask it to build a pod.

**Or run your agent inside Lemma.** `lemma daemon start` connects your local Claude Code, Codex, or OpenCode to the pod: it picks up tasks from a shared queue, streams its work back through the pod, and gets stopped by the same approvals as everyone else. Two agents working the same pod see the same state — a task queue, not a terminal session that evaporates.

```bash
lemma daemon start               # your local agent serves pod-assigned runs
lemma daemon status              # pid, running state, log path
lemma daemon stop
```

Any agent can also operate a pod directly through the CLI:

```bash
lemma table list                 # inspect the data model
lemma record update tasks rec_8f2k --data '{"status": "done"}'
lemma agent run qualifier --input '{"lead_id": "..."}'
lemma workflow start follow-up   # pauses at human approval steps
lemma chat "what's left in the queue?"
```

If you're reading this inside a coding agent session: that agent can work a pod right now.

## Build one with a coding agent

Because a pod is just files, building one is a job a coding agent is already good at: describe the system you want to Claude Code, Codex, or Cursor, let it author the pod directory, and import it. The agent that builds it can also test it — create records, run the workflows, chat with the agents it just defined — because building and operating are the same CLI.

```bash
lemma pod init my-team           # scaffold a starter bundle to edit (or: lemma agent|table|workflow init …)
lemma pod import ./the-pod-your-agent-wrote
lemma apps deploy my-app ./index.html   # deploy a no-build HTML app (or a Vite project dir)
```

Python and TypeScript SDKs (with 25+ React hooks) live in [`lemma-python/`](lemma-python/) and [`lemma-typescript/`](lemma-typescript/). Generating your frontend elsewhere? Back it with a pod — the TypeScript SDK gives any app tables, agents, workflows, and permissions out of the box.

## Repo layout

| Path | Package | License |
|------|---------|---------|
| `lemma-backend/` | FastAPI backend, migrations, and infra Docker Compose | AGPLv3 |
| `lemma-frontend/` | Next.js frontend | AGPLv3 |
| `agentbox/` | Sandboxed agent workspace manager and runtime image | Apache-2.0 |
| `agentbox-client/` | Python client for the AgentBox workspace API | Apache-2.0 |
| `lemma-stack/` | `lemma-stack` — installer and manager for a self-contained local stack | Apache-2.0 |
| `desktop/` | Tauri macOS desktop app (thin shell around the `lemma-stack` supervisor) | AGPLv3 |
| `lemma-cli/` | `lemma-terminal` — the `lemma` CLI and terminal UI | Apache-2.0 |
| `lemma-python/` | `lemma-sdk` — Python SDK | Apache-2.0 |
| `lemma-typescript/` | `lemma-sdk` — TypeScript/JavaScript SDK for Node, browser, and React | Apache-2.0 |
| `lemma-skills/` | Built-in agent skills | Apache-2.0 |
| `docs/` | Installation and setup guides | — |
| `install.sh` | One-line bootstrap installer | — |

No git submodules — everything is a normal directory in one repo.

## Development

For contributing to the platform itself — hot-reload from source:

```bash
git clone https://github.com/lemma-work/lemma-platform.git
cd lemma-platform
make dev         # run backend, frontend, agentbox with live reload
make logs        # tail backend logs
make stop        # stop dev app processes
make stop-all    # also stop dev infra
```

Run `make help` for the full list. The dev stack runs on its own ports
(frontend 3710, backend 8710) so it never collides with an installed
`lemma-stack` stack (3711/8711).

Backend-only commands live in `lemma-backend/`:

```bash
cd lemma-backend
make test
make lint
make migrate
```

See [`docs/installation.md`](docs/installation.md) for the full setup guide,
[`lemma-backend/README.md`](lemma-backend/README.md) for backend details, and
[`lemma-frontend/README.md`](lemma-frontend/README.md) for frontend details.

## Licensing

The Lemma platform uses a dual-licensing model:

**AGPLv3** (server-delivered core):

- `lemma-backend/` — the FastAPI backend
- `lemma-frontend/` — the Next.js frontend and operator UI

These are licensed under the [GNU Affero General Public License v3](LICENSE).
If you modify and offer the software over a network (e.g. a hosted SaaS), you
must release your modified source under the same terms.

**Apache-2.0** (client-side developer tools):

- `agentbox/` — sandboxed agent workspace manager and runtime image
- `agentbox-client/` — Python client for the AgentBox workspace API
- `lemma-stack/` — local stack installer and manager
- `lemma-cli/` — the `lemma` CLI and terminal UI
- `lemma-python/` — the Python SDK
- `lemma-typescript/` — the TypeScript SDK
- `lemma-skills/` — agent skills

These are intended for broad embedding, installation, and adaptation, so they
remain Apache-2.0 and include their own `LICENSE` files.

**Commercial licensing and exceptions** are available from Lemma for
organizations whose procurement policies do not accommodate AGPLv3. The
commercial exception neutralizes the AGPL procurement friction while keeping the
core genuinely open source.

**Trademark:** The Lemma name, logos, and marks are trademarks of Lemma and are
not granted by the software licenses. Fork the code, not the brand.
