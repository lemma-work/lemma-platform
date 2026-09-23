import test from "node:test";
import assert from "node:assert/strict";
import { filterable, isAsking, matching, NO_FILTERS, orderable, ordered, type Filters } from "../src/library/filters.ts";
import { profileTable } from "../src/library/profile.ts";

const NOW = new Date("2026-09-19T10:00:00Z");
const day = (n: number) => new Date(NOW.getTime() + n * 86_400_000).toISOString().slice(0, 10);
const cols = (...names: string[]) => names.map((name) => ({ name, system: name === "id" }));

function pipeline() {
    return Array.from({ length: 12 }, (_, i) => ({
        id: String(i),
        company: "Company " + String.fromCharCode(65 + i),
        stage: ["identified", "trialling", "paid"][i % 3],
        owner: ["Aditi", "Rohan"][i % 2],
        due: day(i - 4),
        seats: (i + 1) * 10,
        note: null,
    }));
}

const profile = () => profileTable(cols("id", "company", "stage", "owner", "due", "seats", "note"), pipeline(), {
    complete: true, now: NOW,
});

const filters = (over: Partial<Filters> = {}): Filters => ({ ...NO_FILTERS, ...over });

test("a column offers a filter only when a row can be one of its values", () => {
    const offered = filterable(profile()).map((c) => c.name);

    assert.ok(offered.includes("stage"));
    assert.ok(offered.includes("owner"));
    assert.ok(offered.includes("due"));
    // A name is different on every row — that is the search box, not a filter.
    assert.ok(!offered.includes("company"));
    assert.ok(!offered.includes("seats"));
    // Empty on every row, so there is nothing to offer.
    assert.ok(!offered.includes("note"));
});

test("nothing chosen keeps everything", () => {
    assert.equal(matching(pipeline(), NO_FILTERS, NOW.getTime()).length, 12);
    assert.equal(isAsking(NO_FILTERS), false);
});

test("values narrow to the ones kept", () => {
    const kept = matching(pipeline(), filters({ values: { stage: ["paid"] } }), NOW.getTime());

    assert.equal(kept.length, 4);
    assert.ok(kept.every((row) => row.stage === "paid"));
});

test("two values on one column are an either-or, two columns are an and", () => {
    const either = matching(pipeline(), filters({ values: { stage: ["paid", "trialling"] } }), NOW.getTime());
    assert.equal(either.length, 8);

    const both = matching(pipeline(), filters({ values: { stage: ["paid"], owner: ["Aditi"] } }), NOW.getTime());
    assert.ok(both.every((row) => row.stage === "paid" && row.owner === "Aditi"));
    assert.ok(both.length < either.length);
});

test("when is asked in words, not in a pair of dates", () => {
    const rows = pipeline();

    assert.ok(matching(rows, filters({ spans: { due: "overdue" } }), NOW.getTime())
        .every((row) => row.due < day(0)));
    assert.deepEqual(matching(rows, filters({ spans: { due: "today" } }), NOW.getTime()).map((r) => r.due), [day(0)]);
    assert.ok(matching(rows, filters({ spans: { due: "later" } }), NOW.getTime())
        .every((row) => row.due > day(7)));
});

test("a row with no date is not overdue, it is unset", () => {
    const rows = [...pipeline(), { id: "x", company: "No date", stage: "paid", owner: "Aditi", due: null, seats: 1, note: null }];

    assert.ok(!matching(rows, filters({ spans: { due: "overdue" } }), NOW.getTime()).some((r) => r.id === "x"));
    assert.ok(!matching(rows, filters({ spans: { due: "later" } }), NOW.getTime()).some((r) => r.id === "x"));
});

test("text still matches anywhere in a row", () => {
    const kept = matching(pipeline(), filters({ text: "company c" }), NOW.getTime());

    assert.equal(kept.length, 1);
    assert.equal(kept[0].company, "Company C");
});

/* ---- order ------------------------------------------------------------- */

const roles = () => new Map(profile().columns.map((c) => [c.name, c.role] as const));

test("dates sort as dates and not as the strings they are stored in", () => {
    const rows = ordered(pipeline(), { column: "due", down: false }, roles());

    assert.deepEqual(rows.map((r) => r.due), [...rows.map((r) => r.due)].sort());
    assert.equal(rows[0].due, day(-4));
});

test("numbers sort as numbers", () => {
    // "100" < "20" as text, and that is what the grid did to every count.
    const rows = ordered(pipeline(), { column: "seats", down: true }, roles());

    assert.deepEqual(rows.map((r) => r.seats), [120, 110, 100, 90, 80, 70, 60, 50, 40, 30, 20, 10]);
});

test("empties go last whichever way it points", () => {
    const rows = [
        { id: "1", due: day(2) },
        { id: "2", due: null },
        { id: "3", due: day(-1) },
    ];
    const role = new Map([["due", "date"]]);

    assert.deepEqual(ordered(rows, { column: "due", down: false }, role).map((r) => r.id), ["3", "1", "2"]);
    assert.deepEqual(ordered(rows, { column: "due", down: true }, role).map((r) => r.id), ["1", "3", "2"]);
});

test("no order leaves the rows exactly as they came", () => {
    const rows = pipeline();
    assert.equal(ordered(rows, null, roles()), rows);
});

test("everything but a paragraph can be sorted by", () => {
    const offered = orderable(profile()).map((c) => c.name);

    assert.ok(offered.includes("company"));
    assert.ok(offered.includes("due"));
    assert.ok(offered.includes("seats"));
});

test("the view can tell whether anything was actually asked", () => {
    assert.equal(isAsking(filters({ text: "  " })), false);
    assert.equal(isAsking(filters({ values: { stage: [] } })), false);
    assert.equal(isAsking(filters({ spans: { due: "any" } })), false);
    assert.equal(isAsking(filters({ spans: { due: "overdue" } })), true);
});
