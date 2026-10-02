import type { SampleTable } from "@/data/sample-tables";
import { interviews } from "./apps/research-model";

/** What is in each sample teammate's space on the landing's demo: the
 *  tables, pages, page views and schedules of one job each, so the product
 *  pages can show every part of a space as a real screen of real-looking
 *  work. Kit runs the feedback loop, Remy a pipeline, Scout a research question and
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

/** A schedule that runs a workflow, switched on for everyone by `owner`
 *  (Priya, unless said): on a cron, when a row is added to `table`, or when a
 *  connected account's `source` sends its `trigger`. */
type When = { cron: string } | { table: string } | { source: string; account: string; trigger: string };
function schedule(pod: string, name: string, workflow: string, when: When, firedDaysAgo: number, owner = "priya-user"): Record<string, unknown> {
    const kind = "cron" in when ? { schedule_type: "TIME", config: { cron: when.cron, timezone: "Europe/London" } }
        : "table" in when ? { schedule_type: "DATASTORE", config: { table_name: when.table, operations: ["INSERT"] } }
        : { schedule_type: "WEBHOOK", config: { source: when.source } };
    const listens = "source" in when ? { account_id: when.account, connector_trigger_id: when.trigger } : { account_id: null, connector_trigger_id: null };
    return {
        id: pod + "-" + name, pod_id: pod, name, ...kind, workflow_name: workflow, workflow_id: "wf-" + workflow, instruction: null,
        filter_instruction: null, filter_output_schema: null, ...listens,
        user_id: owner, visibility: "POD", is_active: true, is_internal: false, paused_by_failures: false,
        last_fired_at: at(-firedDaysAgo), last_fire_status: "TRIGGERED", last_error: null, consecutive_failures: 0,
        created_at: at(-40), allowed_actions: ["schedule.read", "schedule.update"],
    };
}

/* ── Kit: the feedback loop ─────────────────────────────────────────── */

/** The themes Kit has grouped every report into, and how far each fix has
 *  got. Report counts are the rows below, not a number typed twice. */
const THEMES = [
    { id: "large-imports", theme: "Large imports time out", stage: "No fix yet", owner: "Dev", ticket: "LIN-251", pr: "", retried: 0, works: 0,
        quotes: ["14k rows, spinner for 10 min then a timeout. Second time this week.", "Our nightly import of 22k rows fails every time now.", "stuck at 99% on a big CSV"] },
    { id: "bulk-edit", theme: "Bulk edit missing", stage: "No fix yet", owner: "", ticket: "", pr: "", retried: 0, works: 0,
        quotes: ["Changing the owner on 300 rows one by one is brutal.", "Bulk edit would save our ops team hours every week."] },
    { id: "upload-spinner", theme: "Upload spinner never ends", stage: "No fix yet", owner: "", ticket: "", pr: "", retried: 0, works: 0,
        quotes: ["Uploaded a big file and the spinner just keeps going."] },
    { id: "mobile-notifications", theme: "Notifications on mobile", stage: "Fix in progress", owner: "Dev", ticket: "LIN-244", pr: "#490", retried: 0, works: 0,
        quotes: ["I never know when my import is done on my phone. Looks frozen.", "App feels stuck after I start an import on mobile."] },
    { id: "dates-as-text", theme: "Dates import as text", stage: "Merged", owner: "Dev", ticket: "LIN-231", pr: "#482", retried: 0, works: 0,
        quotes: ["My dates came in as 2026-09-14 text, so sorting is broken.", "Same date problem as last week. Twice now.", "dates = text again"] },
    { id: "login-link", theme: "Login link expired", stage: "Closed", owner: "Dev", ticket: "LIN-219", pr: "#466", retried: 14, works: 12,
        quotes: ["The login email link is dead by the time I open it."] },
    { id: "column-mapping", theme: "Column mapping resets", stage: "Closed", owner: "Alex", ticket: "LIN-222", pr: "#471", retried: 10, works: 9,
        quotes: ["Every re-upload forgets my column mapping."] },
    { id: "duplicate-rows", theme: "Duplicate rows on re-import", stage: "Closed", owner: "Dev", ticket: "LIN-225", pr: "#474", retried: 6, works: 6,
        quotes: ["Re-importing doubled every row."] },
    { id: "timezone-emails", theme: "Wrong timezone in emails", stage: "Closed", owner: "Alex", ticket: "LIN-228", pr: "#478", retried: 14, works: 4,
        quotes: ["Digest says 3am, it’s 9am here."] },
];
const REPORTS_PER_THEME: Record<string, number> = {
    "large-imports": 41, "bulk-edit": 26, "upload-spinner": 5, "mobile-notifications": 31, "dates-as-text": 22,
    "login-link": 31, "column-mapping": 27, "duplicate-rows": 22, "timezone-emails": 17,
};
const REPORTERS = ["@maria.k", "@tomasz", "@jun.ho", "@lena.p", "@amir", "@ravi.s", "@dina", "@oskar", "@priya.n", "@felix", "@noor", "@kenji"];
const ENTERPRISE = ["Northfield", "Brightpath", "Kite Labs"];
const SOURCES = ["Slack", "Slack", "Slack", "Email", "In app"];

/** 222 reports this month, the newest first: each one a person, where they
 *  said it, and the theme Kit filed it under. Deterministic, so the counts
 *  on every screen agree with each other. */
const FEEDBACK_REPORTS: SampleTable = {
    name: "feedback_reports",
    detail: "Every piece of feedback from Slack, email and the app, filed under a theme",
    columns: columns("id", "reporter", "source", "plan", "said", "theme_id", "created_at"),
    links: { theme_id: "feedback_themes.id" },
    rows: () => THEMES.flatMap((theme, t) => Array.from({ length: REPORTS_PER_THEME[theme.id] }, (_, i) => {
        const n = t * 7 + i;
        const enterprise = i % 13 === 4;
        return {
            id: theme.id + "-" + (i + 1),
            reporter: enterprise ? ENTERPRISE[n % ENTERPRISE.length] : REPORTERS[n % REPORTERS.length],
            source: theme.id === "mobile-notifications" && i % 3 ? "In app" : SOURCES[n % SOURCES.length],
            plan: enterprise ? "Enterprise" : ["Free", "Pro", "Pro"][n % 3],
            said: theme.quotes[i % theme.quotes.length],
            theme_id: theme.id,
            // The spinner theme started from overnight reports; the rest span the month.
            created_at: theme.id === "upload-spinner" ? at(-0.3 - i * 0.02) : at(-((i * 29 + t * 5) % 30) - 0.1),
        };
    })).sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))),
};

const FEEDBACK_THEMES: SampleTable = {
    name: "feedback_themes",
    detail: "Each problem, its ticket and fix, and who owns it",
    columns: columns("id", "theme", "reports", "stage", "owner", "ticket", "pr", "retried", "works", "created_at"),
    rows: () => THEMES.map(({ quotes: _quotes, ...theme }, i) => ({ ...theme, reports: REPORTS_PER_THEME[theme.id], created_at: at(-30 + i) })),
};

/** The replies drafted when PR #482 merged: one per person who reported
 *  dates importing as text. Enterprise accounts go through Sam. Nothing
 *  goes out until Dev confirms the fix is live. */
const RETRY_REPLIES: SampleTable = {
    name: "retry_replies",
    detail: "Replies to everyone who reported a problem, held until its fix is live",
    columns: columns("id", "reporter", "where", "plan", "goes_via", "state", "pr", "created_at"),
    rows: () => FEEDBACK_REPORTS.rows().filter((row) => row.theme_id === "dates-as-text").map((row, i) => ({
        id: "reply-" + (i + 1), reporter: row.reporter, where: row.source === "Email" ? "email" : "#feedback thread", plan: row.plan,
        goes_via: row.plan === "Enterprise" ? "Sam" : "Kit", state: row.plan === "Enterprise" ? "With Sam" : "Waits for Dev", pr: "#482", created_at: at(-0.45),
    })),
};

const KIT: SampleSpace = {
    tables: [FEEDBACK_THEMES, FEEDBACK_REPORTS, RETRY_REPLIES],
    pages: [
        { path: "/pages/Feedback report, week 40.md", detail: "What users said this week, what got fixed, and what nobody owns", daysAgo: 0, text: `# Feedback report, week 40

222 reports this month across 9 themes, one of them new overnight. Captured as they landed, from Slack, email and the app.

## The headline

Large imports are the biggest unsolved problem: 41 reports, up every day this week. Dev owns it as P0 and has a repro with a 14k row file.

## Closing the loop

31 of the 44 people we asked to retry this week say it works. PR #482 (dates import as text) adds 22 more once Dev confirms it’s live.

## Still open

\`\`\`lemma-view
-- Themes without a fix that has shipped
SELECT "theme", "reports", "stage", "owner" FROM "feedback_themes" WHERE "stage" != 'Closed' ORDER BY "reports" DESC LIMIT 50
\`\`\`

## Nobody owns these yet

- Bulk edit, 26 reports. Two Enterprise renewals mention it (Sam). Needs a decision this week.

## What changed in how I sort

- “App feels stuck” on mobile now goes to Notifications, per Dev. 11 reports moved.
` },
        { path: "/pages/Retry reply.md", detail: "What a person hears when their problem is fixed", daysAgo: 6, text: `# Retry reply

Posted in the thread where the person reported it, once the engineer who shipped the fix confirms it’s live.

> Hi {name}, the problem you reported ({theme}) is fixed and live now ({pr}). Could you try again and tell me if it works?

- Enterprise accounts hear from their CSM instead. Kit drafts, Sam sends.
- Nobody hears twice. A second report of the same problem joins the first reply.
- If they say it still breaks, the theme reopens and its owner is told.
` },
    ],
    query: () => FEEDBACK_THEMES.rows()
        .filter((row) => row.stage !== "Closed")
        .sort((a, b) => Number(b.reports) - Number(a.reports))
        .map(({ theme, reports, stage, owner }) => ({ theme, reports, stage, owner: owner || "No owner" })),
    schedules: [
        schedule("kit", "every_feedback_message", "capture-feedback", { source: "slack", account: "acct-acme-slack", trigger: "slack.message_posted" }, 0.002, "sample-user"),
        schedule("kit", "when_a_fix_merges", "close-the-loop", { source: "github", account: "acct-acme-github", trigger: "github.pull_request_merged" }, 0.47, "sample-user"),
        schedule("kit", "nightly_reconcile", "nightly-reconcile", { cron: "0 2 * * *" }, 0.35, "sample-user"),
    ],
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
