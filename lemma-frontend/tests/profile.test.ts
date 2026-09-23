import test from "node:test";
import assert from "node:assert/strict";
import { chooseForm, profileTable, rankedFields, type Form } from "../src/library/profile.ts";

/** A fixed day, so a table looks the same in March as it does in September. */
const NOW = new Date("2026-09-19T10:00:00Z");

function dayOffset(days: number): string {
    return new Date(NOW.getTime() + days * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

function columns(...names: string[]) {
    return names.map((name) => ({ name, system: name === "id" }));
}

function form(
    names: string[],
    rows: Record<string, unknown>[],
    complete = true,
): { form: Form; around?: string; because: string; searchIsPartial: boolean } {
    return chooseForm(profileTable(columns(...names), rows, { complete, now: NOW }));
}

/* ---- the table that started this ------------------------------------- */

const SCRIPTS = [
    "Open on the whiteboard shot, hold two beats, then cut to the demo. The line is 'you already have the data, you just cannot see it' — say it once, do not repeat it over the b-roll. End on the pricing card with no voiceover at all.",
    "Three questions, one answer: what did it do, why did it do that, what did it cost. Every tool answers the first and hides the other two. Build the whole piece around the third, because nobody expects a cost line and everybody wants one.",
    "Start with the failure. The recording where it picks the wrong table and confidently summarises nothing. Let it run twelve seconds, uncut, no music, then the title card, then the fix. People trust a product that shows the bad take first.",
    "Counter-programming: everyone is posting benchmark tables this month. Post one person doing one real task end to end, in real time, mistakes left in. The length is the point — twenty minutes, no cuts, no narration over the thinking parts.",
];

function contentIdeas() {
    return Array.from({ length: 20 }, (_, i) => ({
        id: String(i),
        hook: "Hook " + i,
        /* Varied on purpose. Twenty rows carrying one identical string is a
           column that says nothing about any row, and the ranking drops it —
           correctly, and not like anything a real table holds. */
        notes: SCRIPTS[i % SCRIPTS.length] + " (take " + i + ")",
        source: ["whatsapp", "twitter", "call", "email", "slack"][i % 5],
        published_at: i % 3 === 0 ? dayOffset(-i) : null,
    }));
}

test("a table whose rows are mostly prose is read, not tabulated", () => {
    // `content_ideas` rendered as a grid put the script — the entire point of
    // the row — into a cell three lines tall, and gave `source` (five real
    // values) a resizable textarea.
    const chosen = form(["id", "hook", "notes", "source", "published_at"], contentIdeas());

    assert.equal(chosen.form, "feed");
    assert.equal(chosen.around, "notes", "the long column is what the form is built around");
});

test("a column with five repeated values is a status, not free text", () => {
    const profile = profileTable(columns("id", "hook", "notes", "source", "published_at"), contentIdeas(), {
        complete: true,
        now: NOW,
    });
    const source = profile.columns.find((c) => c.name === "source");

    assert.equal(source?.role, "enum");
    assert.equal(source?.distinct, 5);
});

/* ---- the three cases the grid gets wrong ------------------------------ */

test("days with numbers on them are a chart", () => {
    const rows = Array.from({ length: 190 }, (_, i) => ({
        id: String(i),
        day: dayOffset(-i),
        signups: 40 + (i % 17),
        revenue: 1200 + i * 3,
    }));

    const chosen = form(["id", "day", "signups", "revenue"], rows);

    assert.equal(chosen.form, "chart");
    assert.equal(chosen.around, "day");
});

test("a short list of dated things to get through is a checklist", () => {
    const rows = Array.from({ length: 26 }, (_, i) => ({
        id: String(i),
        title: "Task " + i,
        due: dayOffset(i - 13),
        done: i < 9,
        owner: "Aditi",
    }));

    const chosen = form(["id", "title", "due", "done", "owner"], rows);

    assert.equal(chosen.form, "checklist");
    assert.equal(chosen.around, "due");
});

test("rows in a handful of named stages are a board", () => {
    const rows = Array.from({ length: 40 }, (_, i) => ({
        id: String(i),
        name: "Deal " + i,
        stage: ["new", "qualified", "proposal", "won"][i % 4],
        amount: 1000 * i,
    }));

    const chosen = form(["id", "name", "stage", "amount"], rows);

    assert.equal(chosen.form, "board");
    assert.equal(chosen.around, "stage");
});

test("named things with a few facts each are cards", () => {
    const rows = Array.from({ length: 30 }, (_, i) => ({
        id: String(i),
        name: "Contact " + i,
        company: "Company " + i,
        email: "c" + i + "@example.com",
    }));

    const chosen = form(["id", "name", "company", "email"], rows);

    assert.equal(chosen.form, "cards");
    assert.equal(chosen.around, "name");
});

/* ---- abstaining, which is most of the value --------------------------- */

test("a page of a large table is not a short list", () => {
    // Twenty-six tasks want a checklist. The first twenty-six of four thousand
    // want the grid, and the two are indistinguishable from the rows alone.
    const rows = Array.from({ length: 26 }, (_, i) => ({
        id: String(i),
        title: "Task " + i,
        due: dayOffset(i - 13),
        done: i < 9,
        owner: "Aditi",
    }));

    assert.notEqual(form(["id", "title", "due", "done", "owner"], rows, false).form, "checklist");
});

test("a due date and an estimate are not a time series", () => {
    // Enough rows, one per date, with a number to plot — every condition a
    // chart asks for, and a chart of estimates against deadlines shows nothing.
    const rows = Array.from({ length: 30 }, (_, i) => ({
        id: String(i),
        title: "Task " + i,
        due: dayOffset(i - 15),
        estimate: 1 + (i % 5),
    }));

    assert.notEqual(form(["id", "title", "due", "estimate"], rows).form, "chart");
});

test("dates that are history are not deadlines", () => {
    const rows = Array.from({ length: 30 }, (_, i) => ({
        id: String(i),
        title: "Signature " + i,
        signed_at: dayOffset(-400 - i),
        countersigned: i % 2 === 0,
    }));

    assert.notEqual(form(["id", "title", "signed_at", "countersigned"], rows).form, "checklist");
});

test("too few rows to scan is a reason to choose cards, not to refuse", () => {
    // A grid of two rows is two rows of truncated cells and a screen of white
    // space. The same two as cards show every field they have.
    const rows = [
        { id: "1", name: "Aditi Sharma", status: "In conversation" },
        { id: "2", name: "Rohan Mehta", status: "New" },
    ];

    assert.equal(form(["id", "name", "status"], rows).form, "cards");
});

test("a table with nothing that identifies a row still gets cards when there is nothing to scan", () => {
    // `ip` is filled, distinct and short — everything a heading is — and a
    // page of cards headed "10.2.0.3" is worse than the grid it replaced.
    const rows = Array.from({ length: 5 }, (_, i) => ({
        id: String(i), ip: "10.2.0." + i, capacity: 100 + i,
    }));

    const chosen = form(["id", "ip", "capacity"], rows);

    assert.equal(chosen.form, "cards");
    assert.equal(chosen.around, undefined, "nothing to build around, and that is fine");
});

test("width decides nothing, because every shape shows a few fields", () => {
    // The grid has always shown five of however many there are. "Twenty-one
    // columns" was never a reason to refuse a shape — it is a reason to rank.
    const names = ["name", ...Array.from({ length: 20 }, (_, i) => "field_" + i)];
    const rows = Array.from({ length: 30 }, (_, i) =>
        Object.fromEntries([["id", String(i)], ...names.map((n) => [n, n + "-" + i])]));

    assert.equal(form(["id", ...names], rows).form, "cards");
});

test("many rows with no name to find them by stay a grid", () => {
    // Forty events are read down a column; twenty-two people are looked for.
    const rows = Array.from({ length: 40 }, (_, i) => ({
        id: String(i),
        actor: ["aditi", "rohan", "priya", "system"][i % 4],
        action: ["records.update", "files.read", "apps.deploy", "agents.run"][i % 4],
        resource: ["table", "file", "app", "agent"][i % 4],
        status: i % 11 === 0 ? "error" : "ok",
        region: ["ap-south-1", "us-east-1"][i % 2],
        duration_ms: 40 + i,
    }));

    assert.equal(form(["id", "actor", "action", "resource", "status", "region", "duration_ms"], rows).form, "grid");
});

test("a board needs one axis, not a pick from five", () => {
    // Every column in an audit log sits in four-or-fewer values. Piling the
    // rows up by whichever was declared first says nothing about any of them.
    const rows = Array.from({ length: 40 }, (_, i) => ({
        id: String(i),
        actor: ["aditi", "rohan", "priya", "system"][i % 4],
        action: ["records.update", "files.read", "apps.deploy", "agents.run"][i % 4],
        resource: ["table", "file", "app", "agent"][i % 4],
        status: i % 11 === 0 ? "error" : "ok",
        region: ["ap-south-1", "us-east-1"][i % 2],
    }));

    assert.notEqual(form(["id", "actor", "action", "resource", "status", "region"], rows).form, "board");
});

/* ---- ranking ----------------------------------------------------------- */

test("fields rank by what they say about a row, not by declaration order", () => {
    const rows = Array.from({ length: 30 }, (_, i) => ({
        id: String(i),
        created_at: dayOffset(-i) + "T09:00:00Z",
        blurb: "note " + (i % 12),
        stage: ["new", "won"][i % 2],
        name: "Deal " + i,
        due: dayOffset(i),
    }));
    const profile = profileTable(columns("id", "created_at", "blurb", "stage", "name", "due"), rows, {
        complete: true, now: NOW,
    });

    const order = rankedFields(profile).map((c) => c.name);

    assert.deepEqual(order.slice(0, 3), ["name", "stage", "due"]);
    assert.equal(order[order.length - 1], "id", "the key sorts last rather than first");
    assert.ok(order.indexOf("created_at") > order.indexOf("blurb"));
});

test("a column that says the same thing on every row is not shown at all", () => {
    // `agent: "lemma-app/0.1"` on forty rows distinguishes none of them from
    // any other, which is the whole job of a field on a card.
    const rows = Array.from({ length: 30 }, (_, i) => ({
        id: String(i), name: "Row " + i, agent: "lemma-app/0.1", note: i % 2 ? "x" : "y",
    }));
    const profile = profileTable(columns("id", "name", "agent", "note"), rows, { complete: true, now: NOW });

    assert.ok(!rankedFields(profile).some((c) => c.name === "agent"));
    assert.ok(rankedFields(profile).some((c) => c.name === "note"));
});

test("an empty column is a label with a dash under it, so it is dropped", () => {
    const rows = Array.from({ length: 30 }, (_, i) => ({
        id: String(i), name: "Row " + i, archived_reason: null,
    }));
    const profile = profileTable(columns("id", "name", "archived_reason"), rows, { complete: true, now: NOW });

    assert.ok(!rankedFields(profile).some((c) => c.name === "archived_reason"));
});

test("a column filled in almost nowhere is dropped once there are rows to be sure", () => {
    const rows = Array.from({ length: 40 }, (_, i) => ({
        id: String(i), name: "Row " + i, override: i === 0 ? "manual" : null,
    }));
    const profile = profileTable(columns("id", "name", "override"), rows, { complete: true, now: NOW });

    assert.ok(!rankedFields(profile).some((c) => c.name === "override"));
});

test("a table with nothing but bookkeeping in it has no shape to choose", () => {
    const rows = Array.from({ length: 10 }, (_, i) => ({
        id: String(i),
        created_at: dayOffset(-i) + "T09:00:00Z",
        pod_id: "pod-1",
    }));

    assert.equal(form(["id", "created_at", "pod_id"], rows).form, "grid");
});

/* ---- honesty about what was actually loaded --------------------------- */

test("search over loaded rows is flagged as partial when rows are missing", () => {
    // The box says "Search loaded rows…" and filters the page in memory. On
    // 4,093 contacts that is a search that quietly misses almost everything.
    const rows = Array.from({ length: 50 }, (_, i) => ({
        id: String(i),
        name: "Contact " + i,
        company: "Company " + (i % 30),
        email: "c" + i + "@example.com",
    }));

    assert.equal(form(["id", "name", "company", "email"], rows, false).searchIsPartial, true);
    assert.equal(form(["id", "name", "company", "email"], rows, true).searchIsPartial, false);
});

test("a numeric primary key is an identity, not a quantity", () => {
    const rows = Array.from({ length: 30 }, (_, i) => ({ id: i, day: dayOffset(-i) }));
    const profile = profileTable(columns("id", "day"), rows, { complete: true, now: NOW });

    assert.equal(profile.columns.find((c) => c.name === "id")?.role, "key");
    // Nothing to plot, so nothing is plotted.
    assert.equal(chooseForm(profile).form, "grid");
});

test("every choice names the shape before explaining itself", () => {

    // by, which is what a grid is for" — leaving the reader to work backwards
    // to "this is a table". The shape comes first now, in a word a person uses.
    const shapes: [string[], Record<string, unknown>[], RegExp][] = [
        [["id", "hook", "notes", "source", "published_at"], contentIdeas(), /^Reading view/],
        [["id", "day", "signups", "revenue"],
         Array.from({ length: 40 }, (_, i) => ({ id: String(i), day: dayOffset(-i), signups: 40 + i, revenue: 900 + i })), /^Chart/],
        [["id", "title", "due", "done"],
         Array.from({ length: 12 }, (_, i) => ({ id: String(i), title: "T" + i, due: dayOffset(i - 4), done: i < 3 })), /^Checklist/],
        [["id", "name", "stage"],
         Array.from({ length: 20 }, (_, i) => ({ id: String(i), name: "D" + i, stage: ["new", "won", "lost"][i % 3] })), /^Board/],
    ];

    for (const [names, rows, starts] of shapes) {
        const chosen = form(names, rows);
        assert.match(chosen.because, starts, chosen.form + ": " + chosen.because);
        assert.ok(!/which is what|asks for|worth showing/.test(chosen.because), "no reasoning-aloud: " + chosen.because);
    }
});

test("the grid says which of its three reasons applies", () => {
    const named = Array.from({ length: 50 }, (_, i) => ({
        id: String(i), name: "Contact " + i, company: "Co " + (i % 30), email: "c" + i + "@x.com",
    }));
    const unnamed = Array.from({ length: 40 }, (_, i) => ({
        id: String(i), actor: ["a", "b", "c", "d"][i % 4], action: ["x", "y", "z", "w"][i % 4],
        resource: ["t", "f", "a", "g"][i % 4], status: i % 11 ? "ok" : "error",
        region: ["p", "q"][i % 2], duration_ms: 40 + i,
    }));
    const fields = ["id", "actor", "action", "resource", "status", "region", "duration_ms"];

    // A table half-loaded says nothing but what it is: the count is in the
    // footer, and "only 50 rows are loaded so far" is an apology for a table
    // that is working fine.
    assert.equal(form(["id", "name", "company", "email"], named, false).because, "Table view.");
    assert.match(form(fields, unnamed, true).because, /no title column/);
    assert.match(
        form(["id", "name", "company", "email"], Array.from({ length: 300 }, (_, i) =>
            ({ id: String(i), name: "Contact " + i, company: "Co " + (i % 30), email: "c" + i + "@x.com" })), true).because,
        /too many to show as cards/);
});

test("four rows with a status are four rows in piles", () => {

    // these rows plainly ask for. The status is what the grouping is for.
    const rows = [
        { id: "1", title: "Draft the brief", status: "todo" },
        { id: "2", title: "Book the studio", status: "doing" },
        { id: "3", title: "Send the invite", status: "todo" },
        { id: "4", title: "Pick the music", status: "done" },
    ];

    const chosen = form(["id", "title", "status"], rows);

    assert.equal(chosen.form, "board");
    assert.equal(chosen.around, "status");
});

test("a column with two values across four rows is a status", () => {
    const rows = Array.from({ length: 4 }, (_, i) => ({ id: String(i), stage: i % 2 ? "open" : "shut" }));
    const profile = profileTable(columns("id", "stage"), rows, { complete: true, now: NOW });

    assert.equal(profile.columns.find((c) => c.name === "stage")?.role, "enum");
});

test("a value of its own for every row is not a pile", () => {
    // Four rows in four stages is four headings with one item under each.
    const rows = Array.from({ length: 4 }, (_, i) => ({
        id: String(i), title: "Thing " + i, stage: ["a", "b", "c", "d"][i],
    }));

    assert.notEqual(form(["id", "title", "stage"], rows).form, "board");
});

test("dated things that already happened are a timeline", () => {

    // row has in common.
    const rows = Array.from({ length: 30 }, (_, i) => ({
        id: String(i),
        title: "Signature " + i,
        signed_at: dayOffset(-400 - i),
        countersigned: i % 2 === 0,
    }));

    const chosen = form(["id", "title", "signed_at", "countersigned"], rows);

    assert.equal(chosen.form, "timeline");
    assert.equal(chosen.around, "signed_at");
});

test("work still to come is a checklist, not a timeline", () => {
    // Same columns, same shape. Only where the dates sit differs.
    const rows = Array.from({ length: 20 }, (_, i) => ({
        id: String(i), title: "Task " + i, due: dayOffset(i - 10), done: i < 6,
    }));

    assert.equal(form(["id", "title", "due", "done"], rows).form, "checklist");
});

test("a measured series stays a chart rather than becoming a timeline", () => {
    const rows = Array.from({ length: 60 }, (_, i) => ({
        id: String(i), day: dayOffset(-i), signups: 40 + (i % 9),
    }));

    assert.equal(form(["id", "day", "signups"], rows).form, "chart");
});

test("people with a created_at are not a history of themselves", () => {
    // `created_at` is bookkeeping: it is about the row, not in it, so a
    // contacts table does not become a timeline of its own sign-ups.
    const rows = Array.from({ length: 30 }, (_, i) => ({
        id: String(i), name: "Contact " + i, company: "Co " + i,
        created_at: dayOffset(-i) + "T09:00:00Z",
    }));

    assert.equal(form(["id", "name", "company", "created_at"], rows).form, "cards");
});

test("a recent history with a category on it is not a checklist", () => {
    // Ten releases shipped over six weeks are as "within the month" as ten
    // tasks due over the next six, and `kind` — feature, fix, rename — passes
    // every structural test for a status. It reported them as "10 left".
    const rows = Array.from({ length: 10 }, (_, i) => ({
        id: String(i),
        title: "Shipped " + i,
        shipped_at: dayOffset(-2 - i * 4),
        kind: ["feature", "fix", "rename"][i % 3],
    }));

    const chosen = form(["id", "title", "shipped_at", "kind"], rows);

    assert.equal(chosen.form, "timeline");
});

test("a nearly finished list of work is still a checklist", () => {
    // Most dates have passed and most rows are ticked, but a boolean says
    // plainly what is done and what is not, whatever the dates do.
    const rows = Array.from({ length: 12 }, (_, i) => ({
        id: String(i), title: "Task " + i, due: dayOffset(i - 10), done: i < 10,
    }));

    assert.equal(form(["id", "title", "due", "done"], rows).form, "checklist");
});

test("the column that identifies a row is found in the values, not a word list", () => {
    // A real pipeline keeps the thing each row is in `company`, and `NAMING`
    // holds name/title/label/summary/subject/headline. The table was told it
    // had "no title column" and handed back a spreadsheet.
    const names = [
        "Warmex Home Appliances", "Handyman Services", "Intugine", "Turant Logistics",
        "Ornate Solar", "Clean Fanatics", "FleetCharge", "Install365", "Binocs",
        "Sigmoid Analytics", "Optibus", "Amber AI", "LEDFlex", "Foxo.club",
    ];
    const rows = names.map((company, i) => ({
        id: "u-" + i,
        source: i % 2 ? "referral" : "telemetry",
        stage: ["identified", "not_now", "paid"][i % 3],
        company,
        owner: "Deepak",
    }));
    const profile = profileTable(columns("id", "source", "stage", "company", "owner"), rows, {
        complete: true, now: NOW,
    });

    assert.equal(profile.columns.find((c) => c.name === "company")?.role, "name");
    assert.notEqual(chooseForm(profile).form, "grid");
});

test("a machine value is not a heading however distinct it is", () => {
    const rows = Array.from({ length: 30 }, (_, i) => ({
        id: String(i),
        ip: "10.2." + i + ".1",
        ref: "3f9056b4-1e2d-4132-b701-02b509" + String(1000 + i),
        count: i,
    }));
    const profile = profileTable(columns("id", "ip", "ref", "count"), rows, { complete: true, now: NOW });

    assert.notEqual(profile.columns.find((c) => c.name === "ip")?.role, "name");
    assert.equal(chooseForm(profile).form, "grid");
});

test("a pipeline whose stages never finish is not a checklist", () => {
    // `ahead` reads the values and is wrong about the commonest work queue
    // there is: twenty-eight accounts whose next action was due last week look
    // exactly like twenty-eight things that happened. The column name says
    // which — `next_action_on` names something still to come.
    const rows = Array.from({ length: 28 }, (_, i) => ({
        id: "u-" + i,
        source: i % 2 ? "referral" : "telemetry",
        segment: ["ops_smb", "ai_startup", "unknown", "oss_team"][i % 4],
        stage: ["identified", "not_now", "paid"][i % 3],
        owner: "Deepak",
        next_action_on: dayOffset(-(i % 8) - 1),
        company: "Company " + String.fromCharCode(65 + i),
        next_action: "Send a built pod for their workflow",
    }));

    const chosen = form(["id", "source", "segment", "stage", "owner", "next_action_on", "company", "next_action"], rows);

    // `identified / not_now / paid` has no finishing value in it, so a
    // checklist would read "28 left of 28" for ever, above 28 tick boxes that
    // can never be ticked. It is a pipeline, and a pipeline is piles.
    assert.equal(chosen.form, "board");
    assert.equal(chosen.around, "stage");
});

test("a queue whose work can finish is still a queue when every date has passed", () => {
    const rows = Array.from({ length: 20 }, (_, i) => ({
        id: String(i),
        title: "Review " + i,
        status: ["unknown", "in review", "cleared"][i % 3],
        next_action_on: dayOffset(-(i % 9) - 1),
    }));

    const chosen = form(["id", "title", "status", "next_action_on"], rows);

    assert.equal(chosen.form, "checklist", "overdue is still ahead-facing, and `cleared` finishes");
    assert.equal(chosen.around, "next_action_on");
});

test("the state a shape is built around is the one that moves", () => {
    // Four columns of four-or-fewer values, and only `stage` is the pipeline.
    // Taking whichever came first offered "referral / telemetry" to set.
    const rows = Array.from({ length: 28 }, (_, i) => ({
        id: "u-" + i,
        source: i % 2 ? "referral" : "telemetry",
        stage: ["identified", "not_now", "paid"][i % 3],
        company: "Company " + String.fromCharCode(65 + i),
        due: dayOffset(-(i % 8) - 1),
    }));
    const profile = profileTable(columns("id", "source", "stage", "company", "due"), rows, {
        complete: true, now: NOW,
    });
    const shown = profile.columns.filter((c) => c.role === "enum" && c.distinct <= 4).map((c) => c.name);
    const chosen = chooseForm(profile);

    assert.deepEqual(shown, ["source", "stage"], "both look like a status structurally");
    assert.equal(chosen.around, "stage", "the one that moves, not the one declared first");
});

test("a date whose name points back does not become work to do", () => {
    const rows = Array.from({ length: 12 }, (_, i) => ({
        id: String(i),
        title: "Shipped " + i,
        shipped_at: dayOffset(-2 - i),
        kind: ["feature", "fix"][i % 2],
    }));

    assert.notEqual(form(["id", "title", "shipped_at", "kind"], rows).form, "checklist");
});

test("a board needs something to write at the top of a card", () => {
    // Forty log lines grouped by `status` is two piles of anonymous rows.
    const rows = Array.from({ length: 40 }, (_, i) => ({
        id: String(i),
        actor: ["aditi", "rohan", "priya", "system"][i % 4],
        action: ["records.update", "files.read"][i % 2],
        status: i % 11 ? "ok" : "error",
        duration_ms: 40 + i,
    }));

    assert.equal(form(["id", "actor", "action", "status", "duration_ms"], rows).form, "grid");
});
