<div align="center">

<img src="lemma-frontend/src/app/icon.svg" alt="Lemma" width="56">

# Lemma

**A multiplayer harness that shapes itself around your work.**

Your people meet it as a teammate: give it a job, share it with everyone, and teach it as you go.

<a href="https://lemma.work"><img src="https://img.shields.io/badge/Try_it_free-5a3fd4?style=for-the-badge" alt="Try Lemma free"></a>
<a href="https://lemma.work/download"><img src="https://img.shields.io/badge/Download_for_macOS-141414?style=for-the-badge&logo=apple&logoColor=white" alt="Download Lemma for macOS"></a>

[Docs](https://lemma.work/docs) · [Discord](https://discord.gg/x2NVyNPQR) · [Templates](https://lemma.work/templates)

</div>

<br>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/Assets/hero-dark.svg">
  <img src="docs/Assets/hero-light.svg" alt="A teammate's space in Lemma: its apps, context and workflows, the people who share it, the agent at work, and the places people ask it from." width="100%">
</picture>

It runs on the Claude Code or Codex subscription you already pay for, Lemma's hosted models, or any OpenAI- or Anthropic-compatible key.

## Get started

**Lemma Cloud.** Sign up at [lemma.work](https://lemma.work). Nothing to install.

**On your Mac.** [Download Lemma](https://lemma.work/download), open it, and choose
**Local**. It installs everything it needs and runs on your machine. See
[installation](docs/installation.md) if something doesn't start.

**On your own server.** Bring up the whole stack on a VM you control, with
HTTPS. See [self-hosting](docs/self-hosting.md).

```bash
git clone https://github.com/lemma-work/lemma-platform.git
cd lemma-platform/deploy/compose && ./bootstrap.sh
# add your model provider key to .env, then:
docker compose up -d
```

**Your coding agent.**
<img src="docs/Assets/Logos/claude.svg" height="16" alt="Claude Code">&nbsp;Claude Code,
<img src="docs/Assets/Logos/codex.svg" height="16" alt="Codex">&nbsp;Codex,
<img src="docs/Assets/Logos/cursor.svg" height="16" alt="Cursor">&nbsp;Cursor,
<img src="docs/Assets/Logos/opencode-logo-light.svg" height="16" alt="OpenCode">&nbsp;OpenCode or
<img src="docs/Assets/Logos/antigravity.png" height="16" alt="Antigravity">&nbsp;Antigravity
can build a whole teammate. Paste this in:

```text
Set up Lemma, then build me a teammate.

1. Install the CLI with `uv tool install lemma-terminal` (not pip; it needs
   Python 3.14 and uv provides it).
2. Run `lemma auth login` and let me finish signing in.
3. Run `lemma skills install`, then tell me to restart you so the skills load.
4. After the restart, use the Lemma builder skills.

Based on what you already know about me, my work and how my team works,
propose the one teammate that would take the most off our plate: its job,
who it works with, and what it should never do without asking. Ask me
anything you can't work out. Once I agree, build it: the tables that hold
the shared state, the agents and workflows that move the work, the app
people open, and the permissions, with a person approving anything
consequential.
```

It writes everything as files, imports them, and runs the workflow to check it
stops where it should.

## Inside a teammate

Each teammate lives in a **pod**: the boundary around one job. A pod holds its
own agents, apps, tables, files and workflows, decides who is inside and what
each person and agent can touch, and nothing crosses it unless you connect it.
It exports as a folder of files, so you can version it, share it, or import it
somewhere else.

## <img src="docs/Assets/icons/agents.svg" width="26" height="26" align="top" alt=""> Agents

An agent is a role: a model, an instruction, and the tools, tables and
connectors it is allowed to use. It answers when someone asks, and it also runs
without being asked, on a schedule, when a row in a table changes, or when a
webhook arrives from Slack, GitHub or anywhere else. Each run is recorded step
by step, so you can see what it read, what it called, and where it stopped.

Before anything consequential, like sending an email, issuing a refund or
deleting a record, it pauses and asks the person who owns that decision. The run
waits, for days if it has to, and carries on when they answer.

## <img src="docs/Assets/icons/apps.svg" width="26" height="26" align="top" alt=""> Apps

A lot of work needs a screen rather than a chat thread: a queue to clear, a
board to move, a form to fill in. Ask the teammate for one and it builds a web
app, deployed at its own URL, that reads and writes the same tables its agents
use. When an agent updates a record, the app is already showing it.

Apps are built on the TypeScript SDK, so a developer can open the code, change
it, and redeploy with `lemma apps deploy`.

## <img src="docs/Assets/icons/people.svg" width="26" height="26" align="top" alt=""> People

A teammate is shared by the whole team, and each person has their own account
and role inside it. One permission model covers people and agents alike: which
tables they can read or write, which rows they can see, and which steps need
their approval. Clients and guests are invited the same way and see only their
part.

Every action lands in one history: who asked, which agent ran, and what changed.

## <img src="docs/Assets/icons/context.svg" width="26" height="26" align="top" alt=""> Context

The teammate keeps its memory as Markdown notes. Shared notes are readable by
everyone on the team; personal notes only by you. Correct it once ("refunds over
$500 go to Sam") and it writes that down and works that way from then on. Any
note can be opened, read and fixed by hand.

Alongside the notes are tables, typed records with row-level security, and
files, the documents and PDFs agents can search. All three are read at run time,
so what the teammate knows is what your team can see and edit.

## <img src="docs/Assets/icons/surfaces.svg" width="26" height="26" align="top" alt=""> Surfaces

People reach the same teammate from Slack, Microsoft Teams, WhatsApp, Telegram
and email. Each surface works out who is writing, matches them to their account,
and applies their permissions, so a question in a Slack thread gets the same
answer it would get in the app. Every agent also has its own email address that
anyone can write to.

Approvals travel too: a workflow waiting on someone can ask them in the chat
they already use.

## <img src="docs/Assets/icons/workflows.svg" width="26" height="26" align="top" alt=""> Workflows

When work passes between people and agents in the same order every time, write
it down as a workflow. Each step is an agent, a function (plain code that does
the same thing every time), a decision, a loop, a wait, or a form for a named
person. A workflow starts on a schedule, a table change, a webhook, or by hand.

Runs are durable. A step waiting on a person can wait for days, and the run
resumes exactly where it stopped.

## Open source

The core is [AGPLv3](LICENSE); the SDKs, CLI and skills are
[Apache-2.0](LICENSES/Apache-2.0.txt). Run it on your laptop, your own server,
or Lemma Cloud. To work on Lemma itself, start with
[CONTRIBUTING.md](CONTRIBUTING.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

Questions go in [Discussions](https://github.com/lemma-work/lemma-platform/discussions)
or [Discord](https://discord.gg/x2NVyNPQR).

<br>

<div align="center">

<img src="lemma-frontend/public/teammates/cast/bolt-sm.webp" width="56" alt="Bolt">
<img src="lemma-frontend/public/teammates/cast/moon-sm.webp" width="56" alt="Moon">
<img src="lemma-frontend/public/teammates/cast/bloom-sm.webp" width="56" alt="Bloom">
<img src="lemma-frontend/public/teammates/cast/kite-sm.webp" width="56" alt="Kite">
<img src="lemma-frontend/public/teammates/cast/gem-sm.webp" width="56" alt="Gem">
<img src="lemma-frontend/public/teammates/cast/spark-sm.webp" width="56" alt="Spark">
<img src="lemma-frontend/public/teammates/cast/cloud-sm.webp" width="56" alt="Cloud">
<img src="lemma-frontend/public/teammates/cast/prism-sm.webp" width="56" alt="Prism">
<img src="lemma-frontend/public/teammates/cast/knot-sm.webp" width="56" alt="Knot">
<img src="lemma-frontend/public/teammates/cast/peak-sm.webp" width="56" alt="Peak">

<sub>© 2026 Folks and Machines, Inc.</sub>

</div>
