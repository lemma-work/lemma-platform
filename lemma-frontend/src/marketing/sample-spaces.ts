import type { SampleTable } from "@/data/sample-tables";
import { interviews } from "./apps/research-model";

/** What is in each sample teammate's space on the landing's demo: the
 *  tables, pages, page views and schedules of one job each, so the product
 *  pages can show every part of a space as a real screen of real-looking
 *  work. Kit runs a launch, Remy a pipeline, Scout a research question and
 *  June customer imports. Isolated from the general fixtures, like the rest
 *  of the preview source; the workflows are in ./preview-fixtures.
 *
 *  Dates are relative to now, so "due Friday" stays true whenever the
 *  screens are captured again. */

const DAY = 86_400_000;
const on = (offset: number): string => {
    const day = new Date(Date.now() + offset * DAY);
    return day.getFullYear() + "-" + String(day.getMonth() + 1).padStart(2, "0") + "-" + String(day.getDate()).padStart(2, "0");
};
const at = (offset: number): string => new Date(Date.now() + offset * DAY).toISOString();
const columns = (...names: string[]) => names.map((name) => ({ name, system: name === "id" || name === "created_at" }));

export interface SamplePage { path: string; detail: string; daysAgo: number; text: string }

export interface SampleSpace {
    tables: SampleTable[];
    pages: SamplePage[];
    /** The rows a page's view block gets back. The sample answers every
     *  query in a space with the one view its pages carry. */
    query(): Record<string, unknown>[];
    /** Standing work, in the wire shape `readSchedules` parses. */
    schedules: Record<string, unknown>[];
    /** Tables with row-level security on: each person sees their own rows. */
    ownRows?: string[];
}

/** A schedule that runs a workflow, switched on by Priya for everyone: on a
 *  cron when given one, otherwise when a row is added to `table`. */
function schedule(pod: string, name: string, workflow: string, when: { cron: string } | { table: string }, firedDaysAgo: number): Record<string, unknown> {
    const kind = "cron" in when ? { schedule_type: "TIME", config: { cron: when.cron, timezone: "Europe/London" } } : { schedule_type: "DATASTORE", config: { table_name: when.table, operations: ["INSERT"] } };
    return {
        id: pod + "-" + name, pod_id: pod, name, ...kind, workflow_name: workflow, workflow_id: "wf-" + workflow, instruction: null,
        filter_instruction: null, filter_output_schema: null, account_id: null, connector_trigger_id: null,
        user_id: "priya-user", visibility: "POD", is_active: true, is_internal: false, paused_by_failures: false,
        last_fired_at: at(-firedDaysAgo), last_fire_status: "TRIGGERED", last_error: null, consecutive_failures: 0,
        created_at: at(-40), allowed_actions: ["schedule.read", "schedule.update"],
    };
}

/* ── Kit: the launch ────────────────────────────────────────────────── */

const LAUNCH_ASSETS: SampleTable = {
    name: "launch_assets",
    detail: "Every asset for the launch, who owns it and where it stands",
    columns: columns("id", "asset", "owner", "status", "due", "launch_id", "created_at"),
    links: { launch_id: "launches.id" },
    rows: () => [
        ["Launch post", "Priya", "In review", 2], ["Pricing page copy", "Aditi", "Drafting", 3],
        ["Demo video, 90s", "Rohan", "Drafting", 5], ["Press release", "Priya", "In review", 4],
        ["Customer quote, Northfield", "Kit", "Waiting on customer", 3], ["Changelog entry", "Kit", "Ready", 1],
        ["Onboarding email", "Aditi", "Ready", 2], ["Social thread", "Kit", "Drafting", 4],
        ["Help centre article", "Rohan", "Ready", 1], ["Sales one-pager", "Aditi", "In review", 5],
        ["Webinar invite", "Kit", "Drafting", 6], ["App store screenshots", "Rohan", "Waiting on customer", 6],
    ].map(([asset, owner, status, due], i) => ({
        id: "asset-" + (i + 1), asset, owner, status, due: on(Number(due)), launch_id: "launch-oct", created_at: at(-12 + i),
    })),
};

const KIT: SampleSpace = {
    tables: [
        LAUNCH_ASSETS,
        {
            name: "readiness_checks",
            detail: "What has to be true before the launch goes out",
            columns: columns("id", "check", "done", "due", "created_at"),
            rows: () => ([
                ["Demo matches the current onboarding", true, 1], ["Pricing page reviewed by finance", true, 2],
                ["Customer names cleared in writing", false, 3], ["Press list confirmed with Priya", true, 3],
                ["Support briefed on the new plan", false, 5], ["Changelog published", true, 6],
                ["Status page has a maintenance note", false, 8],
            ] as const).map(([check, done, due], i) => ({ id: "check-" + (i + 1), check, done, due: on(due), created_at: at(-8 + i) })),
        },
        {
            name: "launches",
            detail: "Each launch, its date and how far along it is",
            columns: columns("id", "name", "ships_on", "stage", "created_at"),
            rows: () => [
                { id: "launch-oct", name: "Team plans", ships_on: on(9), stage: "Assets due", created_at: at(-30) },
                { id: "launch-sep", name: "Usage reports", ships_on: on(-21), stage: "Shipped", created_at: at(-60) },
                { id: "launch-aug", name: "Mobile app", ships_on: on(-48), stage: "Shipped", created_at: at(-90) },
            ],
        },
    ],
    pages: [
        { path: "/pages/Team plans launch.md", detail: "The brief: who it is for and what has to ship", daysAgo: 0, text: `# Team plans launch

Team plans let a whole company share one Acme account, with a seat per person. We ship in nine days.

## Who it is for

Founders who already pay for Acme themselves and now want their team in. The pain we lead with: three people expensing three cards for the same tool.

## What has to ship

\`\`\`lemma-view
-- Assets still open
SELECT "asset", "owner", "status", "due" FROM "launch_assets" WHERE "status" != 'Ready' ORDER BY "due" LIMIT 50
\`\`\`

## Rules for this launch

- Anything public goes to Priya before it goes out.
- No customer name without written permission. Northfield's quote is still waiting on theirs.
- The readiness check runs Thursdays at 9.

## Open questions

- [x] Do annual plans get the seat discount? Yes, agreed with finance.
- [ ] Do we announce on WhatsApp or only email?
` },
        { path: "/pages/Launch day.md", detail: "The hour by hour for launch morning", daysAgo: 1, text: `# Launch day

- [x] Changelog entry published
- [x] Help centre article live
- [ ] Launch post goes out at 10:00
- [ ] Social thread follows at 10:15
- [ ] Sales one-pager sent to the team
- [ ] Watch the support inbox for the first two hours
` },
    ],
    query: () => LAUNCH_ASSETS.rows()
        .filter((row) => row.status !== "Ready")
        .sort((a, b) => String(a.due).localeCompare(String(b.due)))
        .map(({ asset, owner, status, due }) => ({ asset, owner, status, due })),
    schedules: [schedule("kit", "thursday_readiness", "readiness-check", { cron: "0 9 * * 4" }, 0.125)],
};

/* ── Remy: the pipeline ─────────────────────────────────────────────── */

const DEALS: SampleTable = {
    name: "deals",
    detail: "Every buyer, the stage they’re at and the next step",
    columns: columns("id", "company", "buyer", "owner", "stage", "value", "next_step", "created_at"),
    rows: () => ([
        ["Northstar", "Anita Rao", "Procurement", 12000, "Send the approved security overview"],
        ["Cedar Health", "Tom Avery", "Evaluating", 18500, "Hold: Priya spoke to them yesterday"],
        ["Harbor", "Mira Chen", "Won", 9600, "Hand over to June for the first import"],
        ["Birch", "Sam Ortiz", "Discovery", 4800, "Book a call with their ops lead"],
        ["Oak & Ivy", "Lena Fischer", "Evaluating", 7200, "Answer the SSO question"],
        ["Redwing", "Kofi Mensah", "Discovery", 15000, "Send the two-minute walkthrough"],
        ["Saltbox", "Arjun Kapoor", "Procurement", 6400, "Chase the signed order form"],
        ["Moss & Co", "Grace Mwangi", "Won", 5200, "Kickoff booked for Tuesday"],
        ["Fern Works", "Ines Duarte", "Evaluating", 9900, "Share the Harbor case study"],
        ["Pinehurst", "Omar Sayeed", "Discovery", 3600, "Find out who owns the budget"],
    ] as const).map(([company, buyer, stage, value, next], i) => ({
        id: "deal-" + (i + 1), company, buyer, owner: ["Priya", "Remy", "Aditi"][i % 3], stage, value, next_step: next, created_at: at(-30 + i * 2),
    })),
};

const REMY: SampleSpace = {
    tables: [
        DEALS,
        {
            name: "follow_ups",
            detail: "What each buyer is waiting on us for",
            columns: columns("id", "follow_up", "done", "due", "created_at"),
            rows: () => ([
                ["Northstar: security overview to Anita", false, 1], ["Saltbox: chase the order form", false, 2],
                ["Oak & Ivy: SSO answer for Lena", false, 3], ["Harbor: intro Mira to June", true, 0],
                ["Redwing: walkthrough video", true, 1], ["Fern Works: case study", false, 4],
            ] as const).map(([followUp, done, due], i) => ({ id: "fu-" + (i + 1), follow_up: followUp, done, due: on(due), created_at: at(-6 + i) })),
        },
    ],
    pages: [
        { path: "/pages/Pipeline this week.md", detail: "Who’s close, who’s stuck, and why", daysAgo: 0, text: `# Pipeline this week

Two deals can close by Friday. One is stuck on us.

\`\`\`lemma-view
-- Open deals
SELECT "company", "buyer", "stage", "next_step" FROM "deals" WHERE "stage" != 'Won' LIMIT 50
\`\`\`

## Stuck on us

- Northstar asked for the security overview on Tuesday. The approved version is drafted and waiting for your yes.
` },
    ],
    ownRows: ["follow_ups"],
    query: () => DEALS.rows().filter((row) => row.stage !== "Won").map(({ company, buyer, stage, next_step }) => ({ company, buyer, stage, next_step })),
    schedules: [],
};

/* ── Scout: the research question ───────────────────────────────────── */

const INTERVIEWS: SampleTable = {
    name: "interviews",
    detail: "Lost-trial conversations, with the words people used",
    columns: columns("id", "company", "role", "theme", "excerpt", "created_at"),
    rows: () => interviews.map((one, i) => ({ id: one.id, company: one.company, role: one.role, theme: one.theme, excerpt: one.excerpt, created_at: at(-14 + i) })),
};

const SCOUT: SampleSpace = {
    tables: [INTERVIEWS],
    pages: [
        { path: "/pages/Why trials stall.md", detail: "Five conversations, read carefully", daysAgo: 0, text: `# Why trials stall

Five lost trials from September, in their own words. Five is a sample, not a verdict.

## What people said

\`\`\`lemma-view
-- Every conversation
SELECT "company", "role", "theme", "excerpt" FROM "interviews" LIMIT 50
\`\`\`

## What it might mean

- **Setup effort** comes up in three of five. Two of those stalled on the same step: mapping their export.
- **Budget** comes up once, from a team that finished the trial happily. That one is a counterexample, not a pattern.
- **Nobody owned it** at Willow. No feature fixes that.

## What would change our mind

- [ ] Ten conversations from trials that converted, asked the same questions
- [x] June's import error log for the same five accounts

## What we'll try

A guided first import for the next five trials. June runs it, and we read the results here in three weeks.
` },
    ],
    query: () => INTERVIEWS.rows().map(({ company, role, theme, excerpt }) => ({ company, role, theme, excerpt })),
    schedules: [],
};

/* ── June: customer imports ─────────────────────────────────────────── */

const IMPORTS: SampleTable = {
    name: "imports",
    detail: "Every customer’s first file, checked before it runs",
    columns: columns("id", "customer", "file", "rows_in_file", "status", "created_at"),
    rows: () => ([
        ["Harbor", "harbor-customers.csv", 1840, "Needs a fix"], ["Moss & Co", "moss-export.csv", 312, "Imported"],
        ["Fern Works", "fern-crm.xlsx", 96, "Imported"], ["Birch", "birch-contacts.csv", 58, "Checking"],
    ] as const).map(([customer, file, rows, status], i) => ({ id: "imp-" + (i + 1), customer, file, rows_in_file: rows, status, created_at: at(-4 + i) })),
};

const JUNE: SampleSpace = {
    tables: [IMPORTS],
    pages: [],
    query: () => IMPORTS.rows(),
    schedules: [
        schedule("june", "first_files", "first-import-check", { table: "imports" }, 0.2),
        schedule("june", "monday_digest", "onboarding-digest", { cron: "0 9 * * 1" }, 3),
    ],
};

const SPACES: Record<string, SampleSpace> = { kit: KIT, remy: REMY, scout: SCOUT, june: JUNE };

export function spaceOf(podId: string): SampleSpace | undefined {
    return SPACES[podId];
}

export function sampleTableIn(podId: string, name: string): SampleTable | undefined {
    return spaceOf(podId)?.tables.find((table) => table.name === name);
}

export function samplePageIn(podId: string, path: string): SamplePage | undefined {
    return spaceOf(podId)?.pages.find((page) => page.path === path);
}
