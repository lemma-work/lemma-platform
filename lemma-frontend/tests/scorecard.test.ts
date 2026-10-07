import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import {
    BASIC_MEASURES,
    SCORECARD_COLUMNS,
    alwaysChecked,
    countable,
    isProposal,
    latestWeek,
    measureRow,
    readMeasure,
    readWeekResult,
    sayMet,
    sayTarget,
    whyOff,
} from "../src/scorecard/measures.ts";
import { INSTRUCTION_LIMIT, nextReview, reviewInstruction, reviewPagePath, sayDate } from "../src/scorecard/review.ts";
import { planFor, readSuggestion, scheduleName, type Suggestion } from "../src/scorecard/suggestions.ts";
import { readRoleCards } from "../src/data/roles.ts";
import { SAMPLE_ROLES } from "../src/data/sample-roles.ts";
import { barsFor, lastWeek, readPreview, readRows } from "../src/scorecard/history.ts";
import { fixMeasure, unitsOf, wordsToMeasure } from "../src/scorecard/units.ts";

const TEMPLATES = path.resolve(import.meta.dirname, "../../lemma-backend/app/modules/pod_bundle/templates");

/** Enough CSV for the templates' seed files: quoted cells, doubled quotes. */
function parseCsv(text: string): Record<string, string>[] {
    const rows: string[][] = [];
    let row: string[] = [];
    let cell = "";
    let quoted = false;
    for (let at = 0; at < text.length; at++) {
        const char = text[at];
        if (quoted) {
            if (char === "\"" && text[at + 1] === "\"") { cell += "\""; at++; }
            else if (char === "\"") quoted = false;
            else cell += char;
        } else if (char === "\"") quoted = true;
        else if (char === ",") { row.push(cell); cell = ""; }
        else if (char === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; }
        else cell += char;
    }
    if (cell || row.length) { row.push(cell); rows.push(row); }
    const [header, ...body] = rows;
    return body.map((values) => Object.fromEntries(header.map((name, at) => [name, values[at] ?? ""])));
}

function templateMeasures(template: string) {
    const csv = readFileSync(path.join(TEMPLATES, template, "tables/scorecard/data.csv"), "utf8");
    return parseCsv(csv).map((row, at) => readMeasure({ ...row, id: "row-" + at }));
}

test("every template's scorecard has the columns the app writes for a teammate without one", () => {
    // A blank teammate's table is made by the app from SCORECARD_COLUMNS, a
    // role's arrives in its template. The review counts both the same way, so
    // the two must not drift.
    const wanted = SCORECARD_COLUMNS.map((column) => column.name + ":" + column.type).sort();
    for (const template of readdirSync(TEMPLATES)) {
        const table = JSON.parse(readFileSync(path.join(TEMPLATES, template, "tables/scorecard/scorecard.json"), "utf8"));
        const have = (table.columns as { name: string; type: string; auto?: boolean }[])
            .filter((column) => !column.auto)
            .map((column) => column.name + ":" + column.type)
            .sort();
        assert.deepEqual(have, wanted, template);
        assert.equal(table.enable_rls, false, template + ": a scorecard is the whole team's, not one person's");
    }
});

test("every template's seeded measures read as measures, and the ones that are on can be counted", () => {
    for (const template of readdirSync(TEMPLATES)) {
        const measures = templateMeasures(template);
        assert.ok(measures.length > 0, template);
        for (const measure of measures) {
            assert.ok(measure, template + ": a seed row the app cannot read");
            if (measure.on) assert.equal(whyOff(measure), null, template + ": " + measure.key + " is on but cannot be counted");
            else assert.ok(whyOff(measure), template + ": " + measure.key + " is off without a reason to show");
        }
        const keys = measures.map((measure) => measure!.key);
        assert.equal(new Set(keys).size, keys.length, template + ": duplicate keys");
    }
});

test("a role on the shelf is judged on what its template counts", () => {
    // The candidate page lists what the role is judged on before it is hired;
    // the template is what arrives. They are the same list.
    for (const card of readRoleCards(SAMPLE_ROLES)) {
        const on = templateMeasures(card.template)
            .filter((measure) => measure!.on && !alwaysChecked(measure!))
            .map((measure) => measure!.measure);
        assert.deepEqual(card.judgedOn, on, card.template);
    }
});

test("every role's template ships its skills with the frontmatter the deck reads", () => {
    for (const template of readdirSync(TEMPLATES)) {
        const folders = readdirSync(path.join(TEMPLATES, template, "files/skills"));
        assert.ok(folders.length > 0, template);
        for (const folder of folders) {
            const text = readFileSync(path.join(TEMPLATES, template, "files/skills", folder, "SKILL.md"), "utf8");
            assert.match(text, new RegExp("^---\\nname: " + folder + "\\ndescription: "), template + ": " + folder);
        }
    }
});

test("a blank teammate starts with only the checks every teammate gets", () => {
    // What it is judged on comes from what it makes, which nobody has said
    // yet; rows every teammate shares are what made every scorecard look alike.
    for (const measure of BASIC_MEASURES) {
        assert.ok(countable(measure), measure.key);
        assert.ok(alwaysChecked(measure), measure.key + " would show as a row");
        const row = readMeasure({ ...measureRow(measure), id: "x" });
        assert.equal(row?.target, measure.target);
        assert.equal(row?.on, true);
    }
});

test("a target is said in the words of its own kind of number", () => {
    // A share's fractions were once offered for every measure aiming higher,
    // which put "every one" on a goal of reaching 100,000 people.
    assert.equal(sayTarget("share", "higher", 0.9), "9 in 10");
    assert.equal(sayTarget("share", "higher", 1), "every one");
    assert.equal(sayTarget("share", "higher", 0.8), "80%");
    assert.equal(sayTarget("count", "lower", 0), "none");
    assert.equal(sayTarget("count", "lower", 3), "at most 3");
    assert.equal(sayTarget("median", "lower", 30, "minutes"), "at most 30 minutes");
    assert.equal(sayTarget("total", "higher", 25000, "impressions"), "at least 25,000 impressions");
});

test("a total is read as a total, and drawn against its own target", () => {
    const measure = readMeasure({
        id: "r", key: "reach", measure: "LinkedIn posts reach 25,000 people a week", kind: "outcome", counter: "work",
        aim: "higher", target: 25000, counted_from: "LinkedIn posts", is_on: true, position: 1,
        shape: "total", unit_table: "linkedin_posts", time_column: "posted_at", value_unit: "impressions",
    });
    assert.ok(measure);
    assert.equal(measure.shape, "total");
    assert.equal(measure.targetLabel, "at least 25,000 impressions");
    const { bars, target } = barsFor(measure, [
        { status: "counted", shown: "0 impressions", value: 0, met: false, counted: 0, total: 0 },
        { status: "counted", shown: "50,000 impressions", value: 50000, met: true, counted: 50000, total: 9 },
    ]);
    assert.equal(target, 0.5);
    assert.equal(bars[1].height, 1);
});

test("a week's results are read, the newest week wins, and met is counted over what could be judged", () => {
    const rows = [
        { week: "2026-10-03", key: "approved", shown: "8 of 9", met: false },
        { week: "2026-10-10", key: "standing_work", shown: "10 of 10", met: true },
        { week: "2026-10-10T00:00:00Z", key: "approved", shown: "31 of 40", met: false },
        { week: "2026-10-10", key: "callbacks_on_time", shown: "", met: null, status: "nothing_to_count" },
        { week: "not a date", key: "x" },
    ].map(readWeekResult).filter((one) => one !== null);
    assert.equal(rows.length, 4);
    const measures = [
        readMeasure({ id: "a", key: "approved", measure: "Approved", target: 0.9, position: 1 })!,
        readMeasure({ id: "b", key: "standing_work", measure: "On time", target: 1, position: 2 })!,
        readMeasure({ id: "c", key: "callbacks_on_time", measure: "Callbacks", target: 0.9, position: 3 })!,
    ];
    const latest = latestWeek(rows, measures);
    assert.equal(latest?.week, "2026-10-10");
    assert.deepEqual(latest?.results.map((one) => one.key), ["approved", "standing_work", "callbacks_on_time"]);
    assert.equal(sayMet(latest!.results), "1 of 2 on target");
});

test("the review is told to count with the tool and never to make a change itself", () => {
    const words = reviewInstruction("Arch");
    assert.ok(words.length <= INSTRUCTION_LIMIT);
    assert.match(words, /score_week/);
    assert.match(words, /Never compute, estimate or change a number yourself/);
    assert.match(words, /never ask anyone a question/);
    assert.match(words, /review_suggestions/);
    assert.match(words, /Never write the memory, the skill or the schedule yourself/);
});

test("a week's page is named by its end date, with the year", () => {
    assert.equal(sayDate("2026-10-10"), "10 October 2026");
    assert.equal(reviewPagePath("2026-10-10"), "/pages/Week to 10 October 2026.md");
});

test("the next review is the coming Friday at four, or the one after once four has passed", () => {
    const wednesday = new Date(2026, 9, 7, 11, 0);
    const next = nextReview(wednesday);
    assert.equal(next.getDay(), 5);
    assert.equal(next.getDate(), 9);
    assert.equal(next.getHours(), 16);
    const fridayEvening = new Date(2026, 9, 9, 17, 0);
    assert.equal(nextReview(fridayEvening).getDate(), 16);
});

function suggestion(over: Partial<Suggestion>): Suggestion {
    return {
        id: "s1", week: "2026-10-10", kind: "remember", title: "Enterprise accounts are formal", why: "5 edits",
        path: "/memory/enterprise-tone.md", content: "Enterprise accounts: formal, signed by the account owner.", cron: "", status: "open",
        ...over,
    };
}

test("a note is added to what the teammate already wrote, not written over it", () => {
    const fresh = planFor(suggestion({}), null);
    assert.deepEqual(fresh, { kind: "write", path: "/memory/enterprise-tone.md", text: "Enterprise accounts: formal, signed by the account owner.\n" });
    const added = planFor(suggestion({}), "Harbor is on the Enterprise plan.\n");
    assert.equal(added.kind, "write");
    assert.equal((added as { text: string }).text, "Harbor is on the Enterprise plan.\n\nEnterprise accounts: formal, signed by the account owner.\n");
});

test("a note outside the memory folder, or the memory index, is refused", () => {
    for (const where of ["/skills/x/SKILL.md", "/memory/../secrets.md", "/memory/AGENTS.md", "/memory/agents.md", "/memory/a/b.md", "/pages/x.md"]) {
        assert.equal(planFor(suggestion({ path: where }), null).kind, "refuse", where);
    }
});

test("a skill update must name a skill that is there, and must still load", () => {
    const good = "---\nname: refund-requests\ndescription: Handle refund requests within the 30-day window.\n---\n\nThe window is 30 days.";
    assert.equal(planFor(suggestion({ kind: "skill", path: "/skills/refund-requests/SKILL.md", content: good }), "old").kind, "write");
    assert.equal(planFor(suggestion({ kind: "skill", path: "/skills/refund-requests/SKILL.md", content: good }), null).kind, "refuse");
    const renamed = good.replace("name: refund-requests", "name: refunds");
    const refused = planFor(suggestion({ kind: "skill", path: "/skills/refund-requests/SKILL.md", content: renamed }), "old");
    assert.equal(refused.kind, "refuse");
    assert.match((refused as { reason: string }).reason, /would not load/);
});

test("standing work is created only with a cron the platform will run", () => {
    const plan = planFor(suggestion({ kind: "standing_work", title: "Check bugs have steps", cron: "0 9 * * 1-5", content: "Every weekday, check new bugs have steps." }), null);
    assert.deepEqual(plan, { kind: "schedule", name: "check_bugs_have_steps", cron: "0 9 * * 1-5", instruction: "Every weekday, check new bugs have steps." });
    assert.equal(planFor(suggestion({ kind: "standing_work", cron: "* * * * *" }), null).kind, "refuse");
    assert.equal(planFor(suggestion({ kind: "standing_work", cron: "*/5 * * * *" }), null).kind, "refuse");
    assert.equal(planFor(suggestion({ kind: "standing_work", cron: "*/30 * * * *" }), null).kind, "schedule");
    assert.equal(planFor(suggestion({ kind: "standing_work", cron: "every friday" }), null).kind, "refuse");
});

test("a suggestion row the review wrote badly is skipped, not shown half-read", () => {
    assert.equal(readSuggestion({ id: "1", kind: "rule", title: "x" }), null);
    assert.equal(readSuggestion({ id: "1", kind: "remember", title: "  " }), null);
    assert.equal(readSuggestion({ id: "1", kind: "remember", title: "Keep", status: "weird" })?.status, "open");
    assert.equal(scheduleName("Friday: what customers asked!"), "friday_what_customers_asked");
});

test("a work measure reads its unit, test and shape, and a legacy row gets a shape from its aim", () => {
    const work = readMeasure({
        id: "w", key: "first_reply", measure: "First reply inside an hour", counter: "work", target: 0.9, aim: "higher",
        shape: "share", unit_table: "conversations", time_column: "created_at", test: "first_reply_at - created_at <= interval '1 hour'",
        is_on: true, position: 1,
    })!;
    assert.equal(work.unitTable, "conversations");
    assert.equal(whyOff(work), null);
    assert.equal(readMeasure({ id: "x", key: "late", measure: "Late", target: 0, aim: "lower" })!.shape, "count");
    assert.equal(whyOff({ ...work, unitTable: null }), "Says nothing about what to count");
});

test("a proposal is a row with somebody's words that nobody has kept yet", () => {
    const row = readMeasure({ id: "p", key: "flops", measure: "Flops", target: 1, proposed_from: "Tell me when one flops", is_on: false })!;
    assert.ok(isProposal(row));
    assert.equal(isProposal({ ...row, on: true }), false);
});

test("bars draw a share on its own scale and a count against the largest week", () => {
    const share = barsFor({ shape: "share", target: 0.9 }, [
        { status: "counted", shown: "8 of 10", counted: 8, total: 10, value: 0.8, met: false },
        { status: "counted", shown: "9 of 10", counted: 9, total: 10, value: 0.9, met: true },
        { status: "too_few", shown: "too few to judge (3)", counted: 3, total: 3, value: null, met: null },
    ]);
    assert.deepEqual(share.bars.map((bar) => bar.state), ["missed", "met", "none"]);
    assert.equal(share.target, 0.9);
    const count = barsFor({ shape: "count", target: 1 }, [
        { status: "counted", shown: "2", counted: 2, total: 9, value: 2, met: false },
        { status: "counted", shown: "none", counted: 0, total: 7, value: 0, met: true },
    ]);
    assert.equal(count.bars.at(0)!.height, 1);
    assert.ok(count.bars.at(1)!.height > 0, "a week of none still draws a sliver");
    assert.equal(count.target, 0.5);
});

test("the preview and rows answers are read defensively", () => {
    const preview = readPreview({ windows: [{ start: "2026-09-30", end: "2026-10-07" }], measures: [{ key: "a", weeks: [{ status: "counted", shown: "2 of 3", counted: 2, total: 3, value: 0.67, met: false }] }, { weeks: [] }] });
    assert.equal(preview.measures.length, 1);
    assert.equal(lastWeek(preview.measures.at(0)!.weeks), "2 of 3");
    assert.deepEqual(readRows({ rows: [{ id: "1", label: "", link: "", passed: true }] }), [{ id: "1", label: "Untitled", link: null, at: null, passed: true }]);
    assert.deepEqual(readRows(null), []);
});

test("what a teammate makes is read from its work measures and counted from a share's weeks", () => {
    const measures = [
        readMeasure({ id: "1", key: "on_time", measure: "Out by 6 pm", counter: "work", shape: "share", unit_table: "reels", time_column: "published_at", counted_from: "Reels", target: 1, is_on: true })!,
        readMeasure({ id: "2", key: "flops", measure: "Flops", counter: "work", shape: "count", aim: "lower", unit_table: "reels", time_column: "published_at", counted_from: "Reels", target: 1, is_on: true })!,
        readMeasure({ id: "3", key: "standing_work", measure: "On time", counter: "standing_work", kind: "reliability", target: 1, is_on: true })!,
    ];
    const week = (total: number) => ({ status: "counted", shown: "", counted: 0, total, value: 0, met: true });
    const units = unitsOf(measures, new Map([["on_time", { key: "on_time", weeks: [week(5), week(4), week(5), week(3)] }]]));
    assert.deepEqual(units, [{ table: "reels", label: "Reels", count: 17 }]);
    assert.deepEqual(unitsOf(measures, new Map()).at(0)!.count, null);
});

test("a person's words go to the teammate in plain words", () => {
    const ask = wordsToMeasure("Tell me when one “flops”");
    assert.match(ask, /Tell me when one "flops"/);
    assert.match(ask, /proposal/);
    // The person sends this message; the machinery is the tool's to explain.
    assert.doesNotMatch(ask, /try_measure|proposed_from|is_on/);
});

test("a goal that could not be counted goes back to the teammate with the reason", () => {
    const ask = fixMeasure("Reach “100k” a month", "The query must use `{end}`.");
    assert.match(ask, /^Fix the goal “Reach "100k" a month”: it could not be counted\. The query must use `\{end\}`\. /);
    assert.match(ask, /proposal for me to keep\.$/);
});
