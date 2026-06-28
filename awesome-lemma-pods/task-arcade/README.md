<div align="center">

# 🏰 Task Arcade

**Your team's work, built into a world.**

Every task your team clears becomes a permanent object in a shared 3D island — saplings for quick wins, castle gates for shipped milestones. Over a sprint, the skyline *is* the recap. Nobody writes it.

<img src="./assets/banner.gif" alt="Task Arcade — a shared 3D world your team builds by clearing tasks" width="100%">

[![Built with Lemma](https://img.shields.io/badge/built%20with-Lemma-2f8d4d)](https://lemma.work)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue)](./LICENSE)
[![react-three-fiber](https://img.shields.io/badge/3D-react--three--fiber-orange)](https://r3f.docs.pmnd.rs/)
[![Runs on your own Claude/Codex](https://img.shields.io/badge/agent-your%20own%20Claude%20%2F%20Codex-8a63d2)](https://lemma.work)

**[⛏ Walk the live world](https://task-arcade.apps.lemma.work)** · **[★ Fork it on GitHub](https://github.com/lemma-work/lemma-platform)** · **[Read the docs](https://lemma.work)**

</div>

---

Most work software makes effort vanish into a closed ticket. Task Arcade makes the opposite bet: a cleared task becomes something you can **see, walk through, and be proud of.** The recap isn't a report anyone writes — the recap is the world.

And here's the part that matters for *you*: this whole thing — the 3D world, the points economy, the review queue, the AI Quartermaster — is **one Lemma pod.** Tables, functions, an agent, scheduled workflows, and a full 3D app, as a single importable unit. Clone it, import it, deploy the app, and you have a running app. Then rewire it for your team's work.

---

## The loop

**Assigned → Cleared → Placed → Approved → Established.**

1. **Assigned** — a manager creates a task and sets its weight: **15 / 30 / 45 / 60 points.**
2. **Cleared** — a member finishes it; the build catalogue opens at that exact tier.
3. **Placed** — they pick a monument and drop it onto a tile. It grows in, *under construction.*
4. **Approved** — a manager approves → it solidifies and counts. Reject → it collapses to rubble, with a reason logged. **This is the trust beat: nothing is real until a human approves it.**
5. **Established** — permanent in the world. Every object keeps its receipt: *who · what · when.*

---

## Built with — the whole modern stack, as one pod

Task Arcade isn't a demo stitched from glue code. It's a real product built on a real stack — and all of it travels in **one pod bundle** you can fork.

| Layer | Stack | What it does here |
|-------|-------|-------------------|
| **The workspace** | **[Lemma](https://lemma.work)** | The foundation. One pod holds the tables, functions, agent, workflows, schedules, permissions, and the app — humans and AI agents reading/writing the same state. *Not a pile of connectors.* |
| **3D world** | **[three.js](https://threejs.org) · [react-three-fiber](https://r3f.docs.pmnd.rs/) · [drei](https://github.com/pmndrs/drei)** | The isometric island — orthographic diorama camera, auto-orbit, contact shadows, GLB monuments that grow in with spring physics. |
| **Motion** | **[GSAP](https://gsap.com)** | The deterministic autoplay cinematic — the product films its own walkthrough, one take, no mouse. |
| **The app** | **React 19 · TypeScript · Vite 7** | A fast, modern SPA deployed as a Lemma app. Role-gated UI, hash routing, zero-config build. |
| **Live data** | **[TanStack Query](https://tanstack.com/query) · `lemma-sdk`** | Every build, task, and sprint is a live pod record — `useRecords`, `useAuth`, real-time, no custom backend. |
| **Backend logic** | **Python · [Pydantic](https://docs.pydantic.dev) · `lemma-sdk`** | Four deterministic, role-enforced functions. Typed inputs, server-side rules, zero trust by default. |
| **The teammate** | **Your own Claude Code / Codex** | The Quartermaster agent runs on *your* subscription — no separate API key, no per-token bill. |
| **Sound** | **Web Audio** | Wooden *thunk* on place, chime on approve, crumble on reject — the loop has arcade feel. |
| **Assets** | **[Kenney](https://kenney.nl) CC0 kits** | 17 build types across 4 swappable art kits (fantasy-town, urban-city, pirate, cars). Reskin = swap the kit. |

> **Why this matters:** a database, an auth layer, a workflow engine, an agent runtime, and a deployed UI normally mean five services and a weekend of glue. Here it's one directory of files. That's the Lemma bet — [build your own](#build-your-own-pod-with-lemma).

---

## Quickstart — clone to running

**Prereqs:** the `lemma` CLI (`uv tool install lemma-terminal`), `lemma auth login`, and an org (`lemma orgs list`). New to Lemma? The [platform repo](https://github.com/lemma-work/lemma-platform) runs the full stack locally in one command.

```bash
# Grab the pod bundle (it lives inside the lemma-platform monorepo)
git clone https://github.com/lemma-work/lemma-platform
cd lemma-platform/awesome-lemma-pods/task-arcade

# 1. Create the pod in your org
lemma pods create task-arcade --org <your-org>

# 2. Import the bundle (tables, functions, agent, workflows, schedules, surface)
lemma pods import .

# 3. Build + deploy the app
cd apps/task-arcade/source
npm install
npm run build
lemma apps deploy task-arcade ./dist --pod <pod-id> --yes
```

Open the deployed app URL. **The first person to sign in becomes the manager** of an empty world — no invite popup, no auto-join. From there, assign your first task and watch the world grow.

> **Prefer to let your coding agent do it?** Point Claude Code / Codex / Cursor / OpenCode at **[`task-arcade.apps.lemma.work/llms.txt`](https://task-arcade.apps.lemma.work/llms.txt)** — it installs the CLI + builder skills and stands the pod up for you.

> **Local dev of the app:** the app reads `window.__LEMMA_CONFIG__`, injected only when deployed as a Lemma app. For local Vite dev, proxy the config in `vite.config.ts` or develop against a deployed pod. `npm run dev` won't connect to a pod on its own.

---

## What's in the pod

A Lemma pod is a directory of plain files — tables, functions, agents, workflows, permissions, apps. Everything here is imported with `lemma pods import`. Build order follows dependencies: **tables → functions → agent → workflows → schedules → surface → app.**

### Tables (`tables/`)
| Table | Purpose |
|-------|---------|
| `sprints` | Sprint cycles (active / completed) with a points goal |
| `team_members` | Roster — name, email, role (`manager` / `member` / `viewer`), color |
| `catalogue_items` | The build menu — 17 components across 4 point tiers |
| `tasks` | The core table. Unified task **and** placement: status flows `assigned → cleared → under_review → established \| demolished`, with `component` + `world_x/z` filled when placed |
| `agent_actions` | Audit log for the Quartermaster agent (nudges, standups, recaps, hype) |

### Functions (`functions/`)
Deterministic, role-enforced Python entrypoints. Role is resolved from `ctx.user_email` → `team_members.role`.
| Function | Who | What |
|----------|-----|------|
| `assign_task` | manager (or self-assign as member) | Creates a task with points, assignee, sprint |
| `clear_task` | assignee only | Marks a task done — unlocks the catalogue at that tier |
| `place_component` | assignee only | Places a build on a cleared task (sets component + coords, status → `under_review`) |
| `review_task` | manager only | Approve → `established`, reject → `demolished`. Sets reviewer email |

### Agent (`agents/quartermaster/`)
**The Quartermaster** — the coordination agent that runs the whole board so people only do two things: clear work and approve it. Warm but efficient; always in the channel, never noisy. It nudges stalled tasks, posts daily standups, writes end-of-day and weekly recaps, celebrates milestones, and takes channel commands (*"give Maya the onboarding flow, 30 pts"*). Runs on a `WORKSPACE_CLI` toolset with read access to every table + execute on `assign_task`. It **never** approves or rejects — that's always a human decision.

### Workflows (`workflows/`) + Schedules (`schedules/`)
Four scheduled workflows run the coordination layer on cron:
- `daily_standup` (9am weekdays) → morning board snapshot
- `stall_check` (10am weekdays) → nudge stalled tasks (max one per task per day)
- `daily_recap` (5pm Mon–Thu) → end-of-day digest
- `weekly_recap` (5pm Fri) → Friday recap with top builders

Plus `milestone_check` — a DATASTORE trigger that fires the moment a task goes `established`, celebrating first 60-pt builds, build counts, and builder point totals.

### Surface (`surfaces/slack/`)
A Slack surface skeleton (disabled by default). Wire it up by creating a Slack connector account, replacing `account_id` in `slack.json`, and enabling it. See [Lemma's connectors docs](https://lemma.work/docs).

### App (`apps/task-arcade/`)
The product UI — a Vite + React 19 + react-three-fiber isometric 3D app. Primary nav: **World** (the hero — sprint panel + 3D grid + task→place card), **Tasks**, and the **Review** queue; plus **Catalog**, **Stats**, and **Recap** views, and a full-page **Quartermaster** command post at `#/quartermaster`. Role-gated: managers assign + review, members clear + place, viewers read-only.

---

## The feel (what a file tree won't show you)

The craft is in the loop. When you walk the [live world](https://task-arcade.apps.lemma.work):

- **Hover receipts** — glide over any build and a floating card shows the builder's avatar, the task, the points, and the state. Every object is accountable.
- **Place = grow-in** — a placed build springs up with an `easeOutBack` overshoot; it *grows* into the world like a plant.
- **Approve = particle burst** — approving fires a 16-particle burst and solidifies the build; the sprint bar ticks up.
- **Reject = rubble** — a rejected build tumbles into rubble that stays as a visible scar. Proof-of-work you can trust *because* failure is visible too.
- **Keyboard-fast review** — `↑/↓` to move, `A` to approve, `R` to reject. Clear the queue without the mouse.
- **Autoplay cinematic** — a deterministic, hands-free walkthrough for one-take screen recordings.

---

## Seeded demo data (optional)

The pod ships **empty**. To see a populated world immediately — a 6-member team, 4 sprints, 17 catalogue items, 56 tasks with a mix of established builds and pending reviews — run the seed script:

```bash
./seed/seed.sh <pod-id>          # or: ./seed/seed.sh  (if LEMMA_POD_ID is set)
```

**What it does:** drops + recreates the `tasks` table (schema can't be mutated in-place), re-imports functions + the agent + the milestone schedule, then loads the demo records.

> ⚠️ **Never run this on a pod with real data.** It drops the tasks table. To clear seeded data later, re-import the tables empty: `lemma pods import tables/`.

After seeding, rebuild + redeploy the app to see the populated world.

---

## First-user behavior

When the app loads for a brand-new user:

1. **First sign-in** → if `team_members` is empty, the user is auto-created as **manager**. They own the pod.
2. **Subsequent users** → if they're not in `team_members`, they see a clean *"You're not on this team yet — ask the team's manager to invite you"* state. No silent auto-add.
3. **To add a teammate:** a manager inserts a `team_members` row (via the CLI or a future in-app invite), then the teammate refreshes.

A real onboarding wizard (name team → invite → connect sources) is scaffolded in the CSS (`.onboard-*`) but not yet wired — a good place to contribute.

---

## Customize it

This is a template. Make it yours:

| You want to... | Edit... | Then... |
|----------------|---------|--------|
| Change the agent's behavior or tone | `agents/quartermaster/instruction.md` | `lemma pods import agents/quartermaster` |
| Add build components / change tiers | `tables/catalogue_items/catalogue_items.json` + seed data | `lemma pods import tables/catalogue_items` |
| Change the points model | `tables/tasks/tasks.json` (the `points` ENUM) + the 4 functions | Drop + recreate the tasks table (schema is immutable in-place) |
| Reskin the 3D world | swap the Kenney kit in `apps/task-arcade/source/src/App.tsx` + `arcade.css` | `npm run build` + redeploy |
| Wire Slack | `surfaces/slack/slack.json` + create a connector account | `lemma surfaces upsert slack` + `lemma surfaces enable slack` |
| Change the cron schedule | `schedules/*/...json` | `lemma pods import schedules/<name>` |

**Re-import after backend edits:** `lemma pods import <folder>` (partial imports work). **Rebuild + redeploy after app edits.**

---

## Roadmap (contributions welcome)

Task Arcade is built in public. On the way:

- **A face for the Quartermaster** — the agent's persona is written; next it gets a named, illustrated mascot with a consistent voice across the app and Slack.
- **Every surface** — assign, clear, and recap from **Slack, email, and Telegram**. Tasks already carry a `source` field and the UI renders the badges; the Slack surface ships ready to wire.
- **Tasks that find themselves** — the Quartermaster reads an inbound email/message and drafts a task (title, points, assignee) for a manager to confirm.
- **More world juice** — drag-to-place with tile snapping, a time-lapse "rewind the sprint" scrubber, a full catalogue storefront, and the richer sound pack.

---

## Build your own pod with Lemma

Task Arcade is one example of what a Lemma pod can be. A pod is just a directory of files — tables, agents, workflows, permissions, apps — so it's portable: export one, edit it, import it back, or import one somebody else built.

```bash
lemma pod init my-pod           # scaffold a starter bundle
lemma table init tickets        # add a table
lemma agent init triage         # add an agent
lemma agents grant triage tickets:read,write   # grant permissions
lemma pods import .             # ship it
```

The full reference lives in the [Lemma skills](https://github.com/lemma-work/lemma-platform/tree/main/lemma-skills) and at [lemma.work/docs](https://lemma.work/docs). The key rules:

1. **Build in dependency order:** tables → functions → agents → workflows → schedules → surfaces → app.
2. **Zero access by default.** Agents and functions get NO access to anything until you grant it explicitly.
3. **Not everything bundles.** File contents and integration auth don't travel in the bundle — set those up with CLI commands and record the steps in your README.

Clone this pod as a working reference, then strip it down or build up from `lemma pod init`.

---

## Verify your deployment

```bash
lemma pods describe              # full inventory of your pod
lemma functions run assign_task --data '{"title":"test","assignee_email":"you@example.com","points":30,"source":"slack","sprint_id":"<id>"}'
lemma agents chat quartermaster "what's pending my approval?"
lemma query run "SELECT status, count(*) FROM tasks GROUP BY status"
```

---

## License

AGPL-3.0 — see [LICENSE](./LICENSE). If you modify and offer the software over a network, you must release your modified source under the same terms. The Lemma name, logos, and marks are trademarks of Lemma and are not granted by the software license. **Fork the code, not the brand.**
