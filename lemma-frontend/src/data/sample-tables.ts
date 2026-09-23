/** Tables for the sample source.
 *
 *  The sample had one table with two rows in it, which was enough to show that
 *  a grid renders and nothing else. A table's shape is the thing the library
 *  now reads to decide how to draw it, and two rows have no shape — so these
 *  are six tables that are each plainly something: prose, a series, a short
 *  list of work, piles, named things, and one that is honestly just a grid.
 *
 *  Dates are relative to the moment they are asked for. A deadline fixture
 *  written as a literal is a deadline that quietly becomes history, and the
 *  view that sorts on "within the month" would stop choosing itself some time
 *  after whoever wrote it stopped looking. */

export interface SampleTable {
    name: string;
    detail: string;
    columns: { name: string; system?: boolean }[];
    /** `column` → `other_table.column`, exactly as a schema writes it. Without
     *  these the sample has no links, and the half of a record page that is
     *  about what a row is attached to could not be looked at at all. */
    links?: Record<string, string>;
    rows(): Record<string, unknown>[];
}

const DAY = 24 * 60 * 60 * 1000;
/* Local, not `toISOString()`. A deadline written in UTC is a deadline that is
   already a day out for most of the world before anyone opens it. */
const on = (offset: number): string => {
    const day = new Date(Date.now() + offset * DAY);
    return day.getFullYear() + "-" + String(day.getMonth() + 1).padStart(2, "0") + "-" + String(day.getDate()).padStart(2, "0");
};
const at = (offset: number): string => new Date(Date.now() + offset * DAY).toISOString();

function columns(...names: string[]) {
    return names.map((name) => ({ name, system: name === "id" || name === "created_at" }));
}

const SCRIPTS = [
    "Open on the whiteboard, hold two beats, then cut straight to the demo. The line is \"you already have the data, you just can't see it\" — say it once and do not repeat it over the b-roll. End on the pricing card with no voiceover at all, just the hum of the office.",
    "Three questions, one answer. What did the agent do, why did it do that, what did it cost. Every tool we looked at answers the first one and hides the other two. Build the whole piece around the third — nobody expects a cost line and everybody wants one.",
    "Start with the failure. The screen recording where it picks the wrong table and confidently writes a summary of nothing. Let it run twelve seconds, uncut, no music. Then the title card. Then how we fixed it. People trust a product that shows the bad take first.",
    "Counter-programming: everyone is posting benchmark tables this month. Post a video of one person doing one real task end to end, in real time, with the mistakes left in. Length is the point. Twenty minutes, no cuts, no narration over the thinking parts.",
    "The teammate metaphor, argued properly. Not \"it's like a coworker\" as a slogan — actually walk through what changes when the thing has a name, a profile and a badge. Ends on the badge printing. That shot does more work than the script does.",
];

const HOOKS = [
    "You already have the data",
    "Three questions, one answer",
    "Show the bad take first",
    "Twenty minutes, no cuts",
    "Give it a name and a badge",
];

const SOURCES = ["whatsapp", "call", "twitter", "email", "standup"];

export const SAMPLE_TABLES: SampleTable[] = [
    {
        name: "content_ideas",
        detail: "Scripts and hooks, mostly prose",
        columns: columns("id", "hook", "script", "source", "published_at", "created_at"),
        rows: () => Array.from({ length: 15 }, (_, i) => ({
            id: "idea-" + (i + 1),
            hook: HOOKS[i % HOOKS.length] + (i >= HOOKS.length ? " (" + (Math.floor(i / HOOKS.length) + 1) + ")" : ""),
            script: SCRIPTS[i % SCRIPTS.length],
            source: SOURCES[i % SOURCES.length],
            published_at: i % 3 === 0 ? on(-i - 2) : null,
            created_at: at(-i - 4),
        })),
    },
    {
        name: "metrics_daily",
        detail: "One row per day",
        columns: columns("id", "day", "signups", "revenue"),
        rows: () => Array.from({ length: 120 }, (_, i) => {
            const back = 119 - i;
            const wave = Math.sin(i / 9) * 14;
            const weekend = [0, 6].includes(new Date(Date.now() - back * DAY).getUTCDay()) ? -18 : 0;
            return {
                id: "m-" + i,
                day: on(-back),
                signups: Math.max(4, Math.round(52 + wave + weekend + i * 0.35)),
                revenue: Math.round(1180 + i * 21 + Math.sin(i / 5) * 260),
            };
        }),
    },
    {
        name: "launch_tasks",
        detail: "Work before the launch",
        columns: columns("id", "title", "due", "done", "owner"),
        rows: () => [
            ["Cut the sixty-second version", -6, true, "Aditi"],
            ["Final pass on the pricing page", -4, true, "Rohan"],
            ["Swap the hero screenshot for the badge shot", -2, true, "Aditi"],
            ["Write the changelog entry", -1, true, "Priya"],
            ["Re-record the voiceover on scene 3", 0, false, "Rohan"],
            ["Check dark mode on the share pages", 1, false, "Aditi"],
            ["Brief the three launch partners", 1, false, "Priya"],
            ["Schedule the thread for 9am", 2, false, "Rohan"],
            ["Load-test the sign-up path", 3, false, "Dev"],
            ["Turn the waitlist off", 4, false, "Dev"],
            ["Send the customer note", 5, false, "Priya"],
            ["Post the retro", 9, false, "Aditi"],
        ].map(([title, offset, done, owner], i) => ({
            id: "task-" + (i + 1),
            title,
            due: on(offset as number),
            done,
            owner,
        })),
    },
    {
        name: "gtm_targets",
        detail: "Accounts and where they are",
        columns: columns("id", "account", "stage", "value", "owner", "contact_id"),
        links: { contact_id: "contacts.id" },
        rows: () => {
            const stages = ["Researching", "In conversation", "Trialling", "Closed"];
            const names = [
                "Northstar", "Studio Eight", "Kettle & Co", "Brightpath", "Lantern Labs",
                "Fieldhouse", "Marlow Group", "Oxbow", "Pinehurst", "Quarry Digital",
                "Redwing", "Saltbox", "Tessellate", "Underline", "Vantage Row",
                "Westford", "Yardley", "Zephyr Works", "Applecross", "Bellweather",
                "Cairnhill", "Draycott",
            ];
            return names.map((account, i) => ({
                id: "acct-" + (i + 1),
                account,
                stage: stages[(i * 3 + Math.floor(i / 4)) % stages.length],
                value: 4000 + ((i * 2731) % 46000),
                owner: ["Aditi", "Rohan", "Priya"][i % 3],
                contact_id: "c-" + ((i % 22) + 1),
            }));
        },
    },
    {
        name: "contacts",
        detail: "People, with a line each",
        columns: columns("id", "name", "company", "email", "created_at"),
        rows: () => {
            const people = [
                ["Aditi Sharma", "Northstar"], ["Rohan Mehta", "Studio Eight"],
                ["Priya Nair", "Kettle & Co"], ["Daniel Okafor", "Brightpath"],
                ["Mei Lin", "Lantern Labs"], ["Tomas Ruiz", "Fieldhouse"],
                ["Sara Haddad", "Marlow Group"], ["James Whitfield", "Oxbow"],
                ["Ines Duarte", "Pinehurst"], ["Kofi Mensah", "Quarry Digital"],
                ["Elena Petrova", "Redwing"], ["Arjun Kapoor", "Saltbox"],
                ["Nadia Hassan", "Tessellate"], ["Ben Ferreira", "Underline"],
                ["Yuki Tanaka", "Vantage Row"], ["Clara Bosch", "Westford"],
                ["Omar Sayeed", "Yardley"], ["Grace Mwangi", "Zephyr Works"],
                ["Liam O'Donnell", "Applecross"], ["Hana Kowalski", "Bellweather"],
                ["Devi Ramesh", "Cairnhill"], ["Marcus Adeyemi", "Draycott"],
            ];
            return people.map(([name, company], i) => ({
                id: "c-" + (i + 1),
                name,
                company,
                email: name.split(" ")[0].toLowerCase().replace(/[^a-z]/g, "") + "@" + company.toLowerCase().replace(/[^a-z]/g, "") + ".com",
                created_at: at(-i * 3 - 1),
            }));
        },
    },
    {
        name: "audit_log",
        detail: "Wide, and a grid on purpose",
        columns: columns("id", "actor", "action", "resource", "resource_id", "ip", "agent", "status", "duration_ms", "region", "trace_id", "created_at"),
        rows: () => Array.from({ length: 40 }, (_, i) => ({
            id: "log-" + i,
            actor: ["aditi", "rohan", "priya", "system"][i % 4],
            action: ["records.update", "files.read", "apps.deploy", "agents.run"][i % 4],
            resource: ["table", "file", "app", "agent"][i % 4],
            resource_id: "res-" + (100 + i),
            ip: "10.2." + (i % 7) + "." + (i % 251),
            agent: "lemma-app/0.1",
            status: i % 11 === 0 ? "error" : "ok",
            duration_ms: 40 + ((i * 97) % 900),
            region: ["ap-south-1", "us-east-1"][i % 2],
            trace_id: "t-" + (i * 7919).toString(16),
            created_at: at(-i / 24),
        })),
    },
];

/* Big enough to have locked the tab up: read whole so the shape is chosen from
   all of it, drawn a screenful at a time. */
SAMPLE_TABLES.push({
    name: "outreach_queue",
    detail: "900 rows, drawn a screenful at a time",
    columns: columns("id", "company", "stage", "owner", "segment", "next_action_on", "next_action", "domain", "created_at"),
    rows: () => Array.from({ length: 900 }, (_, i) => ({
        id: "o-" + i,
        company: ["Northstar", "Saltbox", "Redwing", "Oxbow", "Marlow", "Cairnhill", "Draycott", "Bellweather"][i % 8] + " " + (100 + i),
        stage: ["identified", "in_conversation", "trialling", "not_now"][i % 4],
        owner: ["Aditi", "Rohan", "Priya"][i % 3],
        segment: ["ops_smb", "ai_startup", "oss_team"][i % 3],
        next_action_on: on(-(i % 14) - 1),
        next_action: ["Send a built pod", "Ask what sits in a spreadsheet", "Reactivation call", "Qualify the champion"][i % 4],
        domain: "co" + i + ".example.com",
        created_at: at(-(i % 60) - 1),
    })),
});

/* A history: dated, named, and nothing left to do about any of it. */
SAMPLE_TABLES.push({
    name: "release_log",
    detail: "What shipped, and when",
    columns: columns("id", "title", "shipped_at", "kind", "owner"),
    rows: () => [
        ["The badge is an object, and it behaves like one", 2, "feature", "Aditi"],
        ["Lem is the teammate, not one of the agents under it", 4, "rename", "Rohan"],
        ["A teammate is issued a badge", 4, "feature", "Aditi"],
        ["The landing page keeps its own route", 9, "fix", "Priya"],
        ["Agents belong on the profile", 11, "feature", "Rohan"],
        ["Dark mode on the share pages", 18, "fix", "Aditi"],
        ["Conversation search across a pod", 24, "feature", "Priya"],
        ["The rail remembers where it was", 31, "fix", "Rohan"],
        ["Voice calls survive a reconnect", 38, "fix", "Aditi"],
        ["File previews for PDFs", 45, "feature", "Priya"],
    ].map(([title, back, kind, owner], i) => ({
        id: "rel-" + (i + 1), title, shipped_at: on(-(back as number)), kind, owner,
    })),
});

/* A checklist whose state is a status rather than a tick — the shape that put
   the due date into the tick's column and overflowed it across the tags. */
SAMPLE_TABLES.push({
    name: "review_queue",
    detail: "Dated work, with a status",
    columns: columns("id", "title", "due", "status", "owner"),
    rows: () => [
        ["Newtechkw", -8, "identified", "Deepak"],
        ["Binocs", -3, "unknown", "Aditi"],
        ["Fieldhouse renewal", 0, "identified", "Rohan"],
        ["Saltbox security review", 2, "in review", "Priya"],
        ["Oxbow data request", 4, "unknown", "Deepak"],
        ["Redwing pricing check", 6, "in review", "Aditi"],
        ["Tessellate onboarding", 9, "cleared", "Rohan"],
        ["Underline contract", 12, "cleared", "Priya"],
    ].map(([title, offset, status, owner], i) => ({
        id: "rev-" + (i + 1), title, due: on(offset as number), status, owner,
    })),
});

/* Four rows and a status: small, and still plainly piles rather than cards. */
SAMPLE_TABLES.push({
    name: "pilot_checks",
    detail: "Four rows, and still a board",
    columns: columns("id", "title", "status", "owner"),
    rows: () => [
        { id: "k-1", title: "Draft the brief", status: "todo", owner: "Aditi" },
        { id: "k-2", title: "Book the studio", status: "doing", owner: "Rohan" },
        { id: "k-3", title: "Send the invite", status: "todo", owner: "Priya" },
        { id: "k-4", title: "Pick the music", status: "done", owner: "Aditi" },
    ],
});

/* Two rows, so the case that sits right at the threshold for choosing a
   shape can be looked at rather than argued about. */
SAMPLE_TABLES.push({
    name: "pilot_accounts",
    detail: "Two rows, and still a shape",
    columns: columns("id", "name", "company", "stage", "email"),
    rows: () => [
        { id: "p-1", name: "Aditi Sharma", company: "Northstar", stage: "In conversation", email: "aditi@northstar.com" },
        { id: "p-2", name: "Rohan Mehta", company: "Studio Eight", stage: "New", email: "rohan@studioeight.com" },
    ],
});

export function sampleTable(name: string): SampleTable | undefined {
    return SAMPLE_TABLES.find((t) => t.name === name);
}

/** A sample table in the shape the schema endpoint would return it. */
export function sampleShape(table: SampleTable) {
    return {
        name: table.name,
        primary_key_column: "id",
        columns: table.columns.map((column) => ({
            name: column.name,
            foreign_key: table.links?.[column.name] ? { references: table.links[column.name] } : null,
        })),
    };
}
