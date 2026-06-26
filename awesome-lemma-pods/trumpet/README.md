# Trumpet

A personal AI executive assistant that tracks commitments (to others, from others, habits, calendar), manages a contact book, and organises notes with a searchable index — all through one conversational assistant called **Mr. Toot**.

This is a complete **Lemma pod** — clone it, import it, deploy the desk, and you have a running app. Rewire it for your own life.

> Built on [Lemma](https://lemma.work) — the open-source workspace where humans and AI agents work as one team. Licensed under [AGPL-3.0](./LICENSE).

---

## Quickstart — clone to running

**Prereqs:**
- The `lemma` CLI: `uv tool install lemma-terminal` (or see the [Lemma platform](https://github.com/wineforyourplate/lemma-platform) repo to run the full stack locally)
- Authenticated: `lemma auth login`
- An org selected or created: `lemma orgs list`

```bash
git clone https://github.com/wineforyourplate/trumpet.git
cd trumpet

# 1. Create the pod in your org
lemma pods create trumpet --org <your-org>

# 2. Import the bundle (tables, functions, agents, workflows, schedules)
lemma pods import .

# 3. (Optional) Seed demo data so the app looks alive on first open
node seed/seed.mjs <pod-id>
# or: LEMMA_POD_ID=<pod-id> node seed/seed.mjs

# 4. Build + deploy the desk app
cd desks/trumpet-desk/source
npm install
npm run build
lemma apps deploy trumpet ./dist --pod <pod-id> --yes
```

Open the deployed app URL. You'll see the Trumpet desk with five tabs: **Home**, **Commits**, **Schedule**, **Notes**, and **You** — and Mr. Toot in a popup chat ready to help.

> **Local dev of the desk:** the desk reads `window.__LEMMA_CONFIG__`, which Lemma injects only when the app is deployed as a Lemma app. For local Vite dev, copy `.env.example` to `.env.local`, fill in your pod ID, and run `npm run dev`. The dev server auto-authenticates using your CLI token.

---

## What's in the pod

A Lemma pod is a directory of plain files — tables, functions, agents, workflows, permissions, apps. Everything here is imported with `lemma pods import`. Build order follows dependencies: **tables → functions → agents → workflows → schedules → desk**.

### Tables (`tables/`)
| Table | Purpose |
|-------|---------|
| `commitments` | The core table — four types: `to_others` (you owe someone), `from_others` (someone owes you), `habit` (personal recurring practice), `calendar` (scheduled event) |
| `people` | Contact book — name, email, phone, role, organization, photo, agent notes |
| `note_index` | Searchable index of notes — title, summary, keywords, category, file path |
| `pings` | Outbound messages sent via Slack or Gmail — tracks thread IDs for reply polling |
| `ping_replies` | Inbound replies to pings — body + received timestamp |

All tables have **RLS enabled** (each member sees only their own rows). For a shared demo pod, set `enable_rls: false` on each table before importing.

### Functions (`functions/`)
Deterministic Python entrypoints that handle external integrations:
| Function | What it does |
|----------|-------------|
| `check_integrations` | Checks which apps (Slack, Gmail, Google Calendar, Outlook, Telegram) are connected |
| `send_ping` | Sends a message to a person via Slack DM or Gmail, logs to `pings` table |
| `import_contacts` | Fetches contacts from Gmail or Slack, deduplicates against `people` table |
| `poll_replies` | Checks Slack and Gmail threads for replies to pings sent in the last N days |

### Agent (`agents/mr-toot/`)
**Mr. Toot** — your personal EA. Runs on a `WORKSPACE_CLI` toolset with read/write access to all tables and execute access to all functions. He tracks commitments, looks up people, saves and finds notes, pings contacts via Slack/Gmail, creates calendar events, and imports contacts — all through natural conversation.

### Workflows (`workflows/`) + Schedules (`schedules/`)
Two scheduled workflows run the automation layer:
- `daily-huddle` (8am weekdays) → Mr. Toot pings team members for standup updates and compiles a daily huddle newsletter
- `poll-replies` (every 2 hours) → checks Gmail and Slack threads for replies to pings

### Desk (`desks/trumpet-desk/`)
The product UI — a Vite + React + TypeScript app with a fixed 1512×1008 canvas, dark/light theme, and Hanken Grotesk typography. Five tabs:
- **Home** — date, greeting summary, today's schedule, today's commitments
- **Commits** — full commitment board with urgency sort, voice summary, poke modal
- **Schedule** — unified schedule (calendar events + habits), habit check-off, team leaderboard
- **Notes** — folder stacks with fan-out animation, sticky-note grid, full-screen note editor
- **You** — profile, contact roster with Gmail/Slack import, integration status

---

## Seeded demo data (optional)

The pod ships **empty**. If you want to see a populated desk immediately — 4 people, 7 commitments, 3 habits, and 13 calendar events — run the seed script:

```bash
node seed/seed.mjs <pod-id>
# or: LEMMA_POD_ID=<pod-id> node seed/seed.mjs
```

The script is **idempotent** — it checks for existing people by name before creating, so re-running won't duplicate data.

After seeding, rebuild + redeploy the desk to see the populated views.

---

## Connectors (optional)

Trumpet can act on external systems — Slack, Gmail, Google Calendar, Outlook, Telegram — through Lemma's connector framework. These are **not bundled** (auth configs are org runtime state).

To enable:
1. Connect the integration in your Lemma org: `lemma connectors ...`
2. The app's **You → Connected** tab shows live status and connect buttons
3. Mr. Toot checks connection status before attempting any external action

Without connectors, the app still works fully for commitment tracking, contacts, and notes — just without the external messaging and calendar sync features.

---

## Customize it

This is a template. Make it yours:

| You want to... | Edit... | Then... |
|----------------|---------|--------|
| Change Mr. Toot's behavior or tone | `agents/mr-toot/instruction.md` | `lemma pods import agents/mr-toot` |
| Add a new commitment type | `tables/commitments/commitments.json` (the `type` ENUM) + relevant functions | Drop + recreate the commitments table (schema is immutable in-place) |
| Change the cron schedule | `schedules/*/<name>.json` | `lemma pods import schedules/<name>` |
| Reskin the desk | `desks/trumpet-desk/source/src/index.css` (CSS variables) + `lib/tokens.ts` | `npm run build` + `lemma apps deploy trumpet ./dist --pod <id> --yes` |
| Change the agent model | Lemma runtime profiles (per pod) | See [Lemma docs](https://lemma.work/docs) |

**Re-import after backend edits:** `lemma pods import <folder>` (partial imports work — you don't have to re-import the whole pod). **Rebuild + redeploy after desk edits.**

---

## Verify your deployment

```bash
lemma pods describe                    # full inventory of your pod
lemma agents chat mr-toot "what do I owe Sarah?"   # test the agent
lemma query run "SELECT type, count(*) FROM commitments GROUP BY type"  # check data
```

---

## Build your own pod with Lemma

Trumpet is one example of what a Lemma pod can be. A pod is just a directory of files — tables, agents, workflows, permissions, apps. That makes pods portable: export one, edit it, import it back, or import one somebody else built.

The fastest way to build your own:

```bash
lemma pod init my-pod           # scaffold a starter bundle
lemma table init tickets        # add a table
lemma agent init triage         # add an agent
lemma agents grant triage tickets:read,write   # grant permissions
lemma pods import .             # ship it
```

The full pod-building reference lives in the [Lemma skills](https://github.com/wineforyourplate/lemma-platform/tree/main/lemma-skills) and at [lemma.work/docs](https://lemma.work/docs).

---

## License

AGPL-3.0 — see [LICENSE](./LICENSE). If you modify and offer the software over a network, you must release your modified source under the same terms. The Lemma name, logos, and marks are trademarks of Lemma and are not granted by the software license. Fork the code, not the brand.
