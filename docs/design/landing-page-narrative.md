# Landing page narrative — rebuild spec

Status: **implemented**
Target: `lemma-frontend/components/landing/`

## Deviations from the spec as written

1. **`WorkSurfaceStrip` kept as-is.** The spec proposed narrowing it to builders
   only; reverted on review. "You reach Lemma from wherever you already work" is a
   hero-level claim, and the surface/builder mix is the point.
2. **§3 was rebuilt entirely — the narrated refund story is gone.** The first
   implementation followed the spec: eight scroll-driven steps walking through a
   fabricated support ticket. It was rejected on review, correctly. A synthetic
   story is not proof, and it read as filler. §3 is now the *actual path a pod
   takes* — five layers, each showing the real product surface rather than a
   narrative beat:

   | # | Layer | What it shows |
   |---|---|---|
   | 01 | Build | Claude Code / Codex / Cursor / in Lemma → `lemma pod import`, resources landing |
   | 02 | Deploy | Live app at a URL + the agents attached to it, with run states |
   | 03 | Invite | Members list: teammates, guests, pending invites, invite link |
   | 04 | Access | Permission matrix — people *and* agents × tables, with grant levels |
   | 05 | Use | Telegram, ChatGPT, and the app itself over one shared pod |

   Each layer carries three "depth" chips so the section shows there is more
   underneath rather than claiming it.
3. **§4 was rebuilt twice more.** The primitives grid (a 3x3 of text cells) and
   then a row-of-pills "system map" were both rejected for explaining nothing.
   Shipped version: an isometric stack, ordered the way the system is actually
   built — **tables and files → functions → agents and workflows → apps and
   surfaces**, all resting on a permissions plate. The stack sticks while detail
   cards scroll past it, each carrying real substance (a table schema with types
   and RLS, a function signature, an agent's read/write/cannot-touch scope, a
   workflow graph pausing at a human step, a permission matrix). Clicking a plane
   or a legend row scrolls that card into view.
4. **ChatGPT and Claude are first-class surfaces.** The surfaces section now
   carries nine doors, and the assistant mock shows real tool calls against the
   pod — one allowed, one stopped at an approval gate — to make the point that
   the assistant is operating the pod, not a copy of it.
5. **§6 uses agent terminal chrome** (per brainless.swerdlow.dev): tabbed Claude
   Code / Codex terminals showing a pod actually being authored, replacing the
   generic typing animation.
6. **§7 is examples only.** The CLI export/import block and the portable-claims
   column were removed; it is now eight pods to install and remix.
7. **`.lp-pod-*` → `.lp-primitives-*`.** `landing-page-polish.css` already defines a
   (dead) `.lp-pod-section`; the new §4 uses a distinct prefix to avoid the collision.
8. **`landing-capability-stories.css` deleted outright** (2,071 lines) — every class
   in it belonged to the dissolved component.

## Known follow-up

A class-level audit of the four landing stylesheets found **306 of 400 classes in
`landing-page.css` orphaned** — overwhelmingly the `lp-crm-*`, `lp-demo-*`,
`lp-campaign-*`, and `lp-architecture-*` families left behind by the deleted
`hero-pod-demo.tsx`. These were *not* removed here: several live in grouped
selectors alongside classes that are still in use, so a blind prefix delete would
take live rules with it. Tracked as separate work.

---

## The problem

The page has five sections and no argument.

**1. The middle beat is missing.** The hero poses a problem — "The software you need
doesn't exist yet" — and the page never answers it. The beat that makes Lemma make
sense (*coding agents can write it now; generated code isn't working team software;
Lemma is that missing system*) is stated in [`README.md:21-25`](../../README.md) and
appears nowhere on the landing page.

**2. Three sections state "what it is" at the same altitude, consecutively.**

| Section | Taxonomy it presents |
|---|---|
| `hero-builds-collage.tsx:27` | Slack agents · Telegram · Internal tools · Codex skins · Portals · Inboxes · Approvals |
| `capability-stories.tsx` | Apps · Agents · Shared state |
| `landing-surfaces.tsx:205` | agents · workflows · data · connectors · UI · permissions · observability · deployment |

The reader recounts primitives three times and progresses zero times.

**3. Nothing says what Lemma *is*, in a sentence.** The closest — "Lemma is the
stack." — sits in the right layer of the drag-slider, hidden behind the divider at
rest. The current subhead ("Build the apps and agents your team actually needs")
would run unchanged on Lovable, Replit, or Retool.

**4. The weakest section is the interactive centerpiece.** The stack slider makes the
reader *drag* to see a claim they never asked about, against an "8+ tools" strawman —
the exact framing our positioning work rejects.

**5. No proof of the loop.** The whole value prop collapses into one sequence: job
arrives → agent reads context → state changes → app updates → pauses for a human →
approves → resumes → logged. The page has fragments of that across four mockups and
never runs it end to end once.

**6. Missing entirely:** open/local/portable (one badge in the eyebrow), and the
export → share → remix loop that is the GTM engine.

**7. Dead code.** `hero-pod-demo.tsx` — 1,678 lines, 66 KB, zero imports.
`podBlocks` — 158 lines, zero references. Seven `footnote` strings that are the same
sentence seven times. A section title generated by `headline.split(" ").slice(0, 2)`.

---

## Structure: before → after

| # | Now | # | Proposed | Job |
|---|---|---|---|---|
| 1 | Hero + collage | 1 | **Hero + collage** | State the thesis in three lines |
| — | — | 2 | **The gap** | Why generated code isn't team software |
| 2 | Capability stories ×3 | 3 | **The journey** | Build → deploy → invite → scope → use |
| — | — | 4 | **Inside a pod** | The primitives, exactly once |
| 3 | Surfaces | 5 | **Surfaces** | Where it shows up |
| 5 | Stack slider | 6 | **Build it with your agent** | The agent authors *and* verifies |
| 4 | Templates<br>6 | Quickstart | 7 | **Portable, remixable, open** | Export/remix + trust |
| 7 | Footer CTA | 8 | **Footer CTA** | Close |

Net: two sections cut, three added, one merge. Every section does exactly one job and
hands off to the next.

---

## §1 — Hero

**Keep:** H1, CTAs, `HeroBuildsCollage`.
**Change:** eyebrow, subhead, `WorkSurfaceStrip`, collage caption.

**Eyebrow**
> `[GitHub] Open source` · The runtime for agent-built software

**H1** — unchanged. It is the strongest line we have.
> The software you need **doesn't exist yet.**

**Subhead** — this carries the missing beat.
> Your coding agent can write it. Lemma makes it real team software — shared data,
> agents, workflows, and approvals. Run it locally, self-hosted, or on Lemma Cloud.

**`WorkSurfaceStrip`** — unchanged. "Use it from · WhatsApp · Telegram · Claude Code ·
Codex · + anywhere you work" mixes surfaces with builders deliberately: *you reach
Lemma from wherever you already are* is a hero-level claim, not a §5 detail. The mix
is the point. §5 goes deep on surfaces; the strip stakes the claim early.

**Collage caption** — currently "What teams build on Lemma / Hover a tile for the
idea." The hint line floats free of the argument. Replace:

> **What teams build on Lemma**
> Every tile is a pod — app, data, agents, and workflows in one place.

---

## §2 — The gap  *(new; replaces the stack slider)*

Kicker: **Why generated code isn't team software**

**H2**
> Coding agents write code. Teams need software that **runs.**

Three beats, no primitives list:

1. **A coding agent ships a codebase.**
   It's real code, and it works on the machine it was built on.
2. **A team needs something else.**
   Shared data that outlives the session. Permissions. A place work pauses for a
   person. Somewhere agents keep working when nobody's watching.
3. **Lemma is that layer.**
   Your agent authors the whole system — tables, agents, workflows, permissions, and
   the app. Lemma runs it.

Closing line, full width:
> People use the app. Agents work through the same state and workflows.

No strawman, no screenshots, no drag interaction. This is the sentence the page has
been missing, and it earns everything after it.

---

## §3 — The journey  *(new; shipped)*

Kicker: **From a prompt to running team software**

**H2**
> Build it, ship it, **and hand it to your team.**

**Sub**
> Five layers. Each one is a real part of the product, not a step in a story.

Five alternating bands, each with its own accent from the hero-collage palette and a
mock of the real surface for that layer:

| # | Layer | Accent | Mock |
|---|---|---|---|
| 01 | Build it where you already work | cream | Source chips (Claude Code, Codex, Cursor, or in Lemma) → terminal running `lemma pod import`, resources checking in |
| 02 | The app and its agents go live together | green | Live app card with URL and version + agent list with run states |
| 03 | Bring your team. And anyone else who needs it | purple | Members panel: owner, members, pending invite, invite link |
| 04 | Decide exactly what each one can touch | yellow | Permission matrix: people **and** agents × tables, with grant pills |
| 05 | Use it from wherever you already are | blue | Telegram, ChatGPT, and the app side by side over one pod |

Each layer also carries three short "depth" chips (e.g. *Per-table grants · Resource
visibility · Approval gates on the risky steps*) so the section shows that there is
more underneath each layer instead of asserting it.

## §4 — Inside a pod

Kicker: **Everything lives in a pod**

**H2**
> One boundary for the data, the agents, the rules, and the app.

**Sub**
> A pod is a self-contained environment for one person, team, or process. Export it as
> files. Import it anywhere.

Nine-cell grid — the taxonomy, stated once, after the reader has earned it. Copy
tightened from the README's `Inside a pod` table:

| Primitive | Copy |
|---|---|
| **Tables** | Typed, queryable data with row-level security. Owned by the pod, readable by agents. |
| **Files** | Markdown memory — playbooks, preferences, notes. Searchable and permission-scoped. |
| **Agents** | Workers with a role, tool grants, and access scoped to specific tables and connectors. |
| **Workflows** | Agents, functions, decisions, waits, and human approvals. Triggered by schedule, webhook, table event, chat, or API. |
| **Functions** | Predictable validators and transitions, alongside agent judgment. |
| **Permissions** | Roles for people *and* agents. Table grants, resource visibility, delegation. |
| **Approvals** | Steps that pause, route to a person, and resume on their decision. |
| **Apps** | The interface people open — built on the same APIs the agents use. |
| **Connectors** | The accounts the pod can use to do work. |

This one grid replaces all three competing taxonomies.

---

## §5 — Surfaces

Keep the tab picker and all four mockup renderers. Two data fixes:

**Fix the title hack.** [`landing-page.tsx:153`](../../lemma-frontend/components/landing/landing-page.tsx)
splits every headline at word 2 to fake an accent span. Replace with explicit fields
on `surfaceModes`:

```ts
headlineLead: "Slack approvals,"
headlineTail: "no extra tab."
```

**Collapse seven footnotes into one section line.** All seven `footnote` values are
the same sentence with the product name swapped. Delete the field; render once:

> These are surfaces, not the system. The workflow, data, permissions, and audit trail
> stay in the pod.

---

## §6 — Build it with your coding agent

Merges the orphaned prompt buttons (currently stranded inside capability story 01)
with the quickstart terminal.

Kicker: **Build it**

**H2**
> The agent you already use builds the **whole system.**

**Sub**
> Not just the frontend. Tables, agents, workflows, permissions, and the app —
> authored as files, then verified by the same agent that wrote them. Building and
> operating use the same CLI.

**Left:** harness logos (Claude Code · Codex · Cursor · OpenCode · Antigravity), then
the two existing copy-prompt buttons.

**Right:** `TypingTerminal`, unchanged, with the three steps:
1. Install the CLI
2. Create a pod from a starter
3. Run the app and inspect the workflow

---

## §7 — Portable, remixable, open  *(new)*

Kicker: **Yours to run**

**H2**
> A pod is files. Export it, share it, **remix it.**

**Left half** — the loop, made concrete:

```bash
lemma pod export ./support-desk    # the whole system, as files
lemma pod import ./support-desk    # ship it back — or to another machine
```

Then the four template cards, reframed. Current label "Review and install" becomes
**"Install and remix."** Ten public pods exist in `lib/templates/catalog.ts` —
Roundtable, Panini, Frontdesk, Smart Inbox, Sidekick, Lemma Design, Nachiketa, Drop,
Meal, Lemma GTM — surfaced with a link to `/templates`.

> Complete working pods — app, data, agents, and workflows. Install one, then make it
> yours.

**Right half** — four trust claims, currently absent from the page entirely:

- **Your machine.** The full stack runs on your laptop.
- **Your cloud.** Self-host it, or use Lemma Cloud.
- **Your models.** Your Claude Code or Codex subscription through the daemon,
  Lemma-managed models, or any Anthropic- or OpenAI-compatible endpoint.
- **Your code.** AGPLv3 core; Apache-2.0 SDKs, CLI, and skills.

---

## §8 — Footer CTA

Current closer repeats the subhead verbatim ("Run your apps and agents. Bring your
team."), so the page ends where it started.

**H2**
> Build the software your team actually needs.

**Sub**
> Open source. Running on your laptop in five minutes.

CTAs unchanged.

---

## Kill list

| Target | Size | Reason |
|---|---|---|
| `hero-pod-demo.tsx` | 1,678 lines / 66 KB | Zero imports. Fully dead. |
| `podBlocks` in `landing-data.ts:1-158` | 158 lines | Zero references. |
| `StackComparison` in `landing-surfaces.tsx:156-262` | 107 lines | Section cut. |
| `#replace` section + `stackCompare` state | `landing-page.tsx:35,254-262` | Section cut. |
| `public/landing-page/stack-compare/*.png` | **2.0 MB** | Section cut. Perf win. |
| `footnote` ×7 + render at `landing-surfaces.tsx:151` | — | Same sentence seven times. |
| `.split(" ").slice(0, 2)` at `landing-page.tsx:153-156` | — | String hack driving a section title. |
| `capability-stories.tsx` | 555 lines | Dissolved: prompts → §6, visuals harvested → §3. |

CSS audit follows in `styles/features/landing-page.css` (11,555 lines): `.lp-stack-*`
and the retired `.lp-story-*` rules.

---

## Build order

1. Delete dead code (`hero-pod-demo.tsx`, `podBlocks`) — zero risk, immediate.
2. §2 The gap — new, self-contained, replaces the slider.
3. §4 Inside a pod — new grid, retires the competing taxonomies.
4. Re-order + rewrite copy across §1, §5, §6, §7, §8.
5. §3 The journey — largest build, five layers with their own mocks.
6. CSS audit and prune.

Steps 1–4 land the coherence fix. Step 5 lands the proof.
