import { describe, expect, it } from "vitest";
import { applyChanges, checklistOrder, countLine, fixedValues, pileOrder, readTable, series, timelineDays, withMade } from "../src/areas/tables/layouts";
import { tablesSample } from "../src/areas/tables/sample";
import type { Row } from "../src/areas/tables/source";
import { filterable, isAsking, matching, NO_FILTERS, orderable, ordered } from "@web/library/filters";
import { profileTable } from "@web/library/profile";
import type { RowPages } from "@web/library/record-cache";
import { inwardLinks, outwardLinks } from "@web/library/relations";
import { flagsOn, readSiteConfig } from "../src/session/flags";
import { SAMPLE_LEMMAS } from "../src/data/sample";

const [SUPPORT, LAUNCH, , , BOOKS] = SAMPLE_LEMMAS;

async function readAll(podId: string, name: string) {
    const source = tablesSample();
    const shape = await source.shape(podId, name);
    const rows: Row[] = [];
    let page: string | undefined;
    do {
        const read = await source.rows(podId, name, page);
        rows.push(...read.items);
        page = read.next ?? undefined;
    } while (page);
    const count = await source.count(podId, name);
    return { shape, rows, count };
}

function read(shape: { columns: { name: string; system?: boolean }[]; primary_key_column: string }, rows: Row[], count: number | null, extra: Partial<Parameters<typeof readTable>[0]> = {}) {
    return readTable({ declared: shape.columns, rows, primaryKey: shape.primary_key_column, count, hasNext: false, asRows: false, chosen: null, filters: NO_FILTERS, search: "", order: null, ...extra });
}

describe("the layout a table is shown in", () => {
    it.each([
        [SUPPORT.id, "tickets", "board", "status"],
        [SUPPORT.id, "customers", "cards", "name"],
        [SUPPORT.id, "replies", "feed", "body"],
        [LAUNCH.id, "launch_checklist", "checklist", "due"],
        [LAUNCH.id, "releases", "timeline", "shipped_at"],
        [BOOKS.id, "daily_revenue", "chart", "day"],
    ])("chooses the web's layout for the sample %s %s: %s around %s", async (podId, name, form, around) => {
        const { shape, rows, count } = await readAll(podId, name);
        const table = read(shape, rows, count);
        expect(table.settled).toBe(true);
        expect(table.choice?.form).toBe(form);
        expect(table.choice?.around).toBe(around);
        expect(table.shaped?.form).toBe(form);
    });

    it("shows plain rows for a long table with no name column, read whole across pages", async () => {
        const { shape, rows, count } = await readAll(BOOKS.id, "invoices");
        expect(rows).toHaveLength(140);
        const table = read(shape, rows, count);
        expect(table.choice?.form).toBe("grid");
        expect(table.shaped).toBeNull();
        expect(table.visible[0]).toBe("customer");
    });

    it("waits for the rest of a small table before choosing, and does not wait past the cap", () => {
        const rows = [{ id: "1", title: "a" }];
        expect(read({ columns: [], primary_key_column: "id" }, rows, 40, { hasNext: true }).settled).toBe(false);
        expect(read({ columns: [], primary_key_column: "id" }, rows, 4000, { hasNext: true }).settled).toBe(true);
        expect(read({ columns: [], primary_key_column: "id" }, rows, 4000, { hasNext: true }).choice?.form).toBe("grid");
        expect(read({ columns: [], primary_key_column: "id" }, rows, null, { hasNext: true }).settled).toBe(false);
    });

    it("goes back to plain rows when asked, with every column ranked", async () => {
        const { shape, rows, count } = await readAll(SUPPORT.id, "tickets");
        const table = read(shape, rows, count, { asRows: true });
        expect(table.choice?.form).toBe("board");
        expect(table.shaped).toBeNull();
        expect(table.visible.slice(0, 2)).toEqual(["subject", "status"]);
        expect(table.visible).toContain("id");
        expect(read(shape, rows, count, { asRows: true, chosen: ["status"] }).visible).toEqual(["status"]);
    });

    it("reads the seeded Support Lemma's tasks as a checklist and projects as cards", () => {
        const now = new Date(2026, 9, 24, 12);
        const tasks = {
            primary_key_column: "id",
            columns: ["id", "created_at", "updated_at", "title", "estimate", "due", "done", "status", "project_id"].map((name) => ({ name, system: ["id", "created_at", "updated_at"].includes(name) })),
        };
        const rows = [
            { title: "Photograph the floor lamps", estimate: 3, due: "2026-11-02", status: "doing", done: false, project_id: "p1" },
            { title: "Write the lamp descriptions", estimate: 2, due: "2026-11-04", status: "todo", done: false, project_id: "p1" },
            { title: "Price the new shades", estimate: 1, due: "2026-11-05", status: "todo", done: false, project_id: "p1" },
            { title: "Draft the 30-day rule", estimate: 2, due: "2026-10-28", status: "done", done: true, project_id: "p2" },
            { title: "Ask Dev about broken-in-transit", estimate: 0.5, due: "2026-10-30", status: "todo", done: false, project_id: "p2" },
        ].map((row, index) => ({ id: `t${index}`, created_at: "2026-10-20T10:00:00Z", updated_at: "2026-10-20T10:00:00Z", ...row }));
        const checklist = read(tasks, rows, 5, { now });
        expect(checklist.choice?.form).toBe("checklist");
        expect(checklist.choice?.around).toBe("due");

        const projects = read({ primary_key_column: "id", columns: [{ name: "id" }, { name: "title" }, { name: "owner" }] }, [
            { id: "p1", title: "Spring catalogue", owner: "Maria" },
            { id: "p2", title: "Returns policy", owner: "Dev" },
        ], 2, { now });
        expect(projects.choice?.form).toBe("cards");
    });
});

describe("asking a table something", () => {
    const now = new Date(2026, 9, 24, 12);
    const rows: Row[] = [
        { id: "1", title: "Ship it", status: "todo", due: "2026-10-23", done: false, estimate: 3 },
        { id: "2", title: "Test it", status: "doing", due: "2026-10-24", done: false, estimate: null },
        { id: "3", title: "Plan it", status: "done", due: "2026-10-28", done: true, estimate: 1 },
        { id: "4", title: "Sell it", status: "todo", due: "2026-12-01", done: false, estimate: 8 },
        { id: "5", title: "Price it", status: "todo", due: null, done: false, estimate: 2 },
    ];
    const profile = profileTable([{ name: "id" }, { name: "title" }, { name: "status" }, { name: "due" }, { name: "done" }, { name: "estimate" }], rows, { complete: true, now });
    const roles = new Map(profile.columns.map((column) => [column.name, column.role] as const));

    it("offers a status's values, a date's spans and a tick's two states as filters, and no name", () => {
        const offered = filterable(profile).map((column) => [column.name, column.role]);
        expect(offered).toEqual([["status", "enum"], ["due", "date"], ["done", "boolean"]]);
        expect(orderable(profile).map((column) => column.name)).toContain("estimate");
    });

    it("keeps the rows that match every filter and the search together", () => {
        expect(matching(rows, { ...NO_FILTERS, values: { status: ["todo"] } }, now.getTime()).map((row) => row.id)).toEqual(["1", "4", "5"]);
        expect(matching(rows, { ...NO_FILTERS, values: { status: ["todo", "doing"] }, spans: { due: "overdue" } }, now.getTime()).map((row) => row.id)).toEqual(["1"]);
        expect(matching(rows, { ...NO_FILTERS, spans: { due: "today" } }, now.getTime()).map((row) => row.id)).toEqual(["2"]);
        expect(matching(rows, { ...NO_FILTERS, spans: { due: "week" } }, now.getTime()).map((row) => row.id)).toEqual(["2", "3"]);
        expect(matching(rows, { ...NO_FILTERS, values: { done: ["true"] } }, now.getTime()).map((row) => row.id)).toEqual(["3"]);
        expect(matching(rows, { ...NO_FILTERS, values: { status: ["todo"] }, text: "SELL" }, now.getTime()).map((row) => row.id)).toEqual(["4"]);
        expect(isAsking(NO_FILTERS)).toBe(false);
        expect(isAsking({ ...NO_FILTERS, spans: { due: "any" } })).toBe(false);
        expect(isAsking({ ...NO_FILTERS, text: " it " })).toBe(true);
    });

    it("sorts dates as dates and numbers as numbers, with the empties last either way", () => {
        expect(ordered(rows, { column: "due", down: false }, roles).map((row) => row.id)).toEqual(["1", "2", "3", "4", "5"]);
        expect(ordered(rows, { column: "due", down: true }, roles).map((row) => row.id)).toEqual(["4", "3", "2", "1", "5"]);
        expect(ordered(rows, { column: "estimate", down: true }, roles).map((row) => row.id)).toEqual(["4", "1", "5", "3", "2"]);
        expect(ordered(rows, { column: "title", down: false }, roles).map((row) => row.id)).toEqual(["3", "5", "4", "1", "2"]);
        expect(ordered(rows, null, roles)).toBe(rows);
    });

    it("applies search, filters and order in one reading", () => {
        const table = readTable({
            declared: undefined, rows, primaryKey: "id", count: 5, hasNext: false, asRows: false, chosen: null,
            filters: { ...NO_FILTERS, values: { status: ["todo"] } }, search: "it", order: { column: "estimate", down: false }, now,
        });
        expect(table.matches.map((row) => row.id)).toEqual(["5", "1", "4"]);
    });

    it("says the count as one sentence", () => {
        expect(countLine(true, 3, 22, 22)).toBe("3 of 22 rows match");
        expect(countLine(false, 100, 100, 4093)).toBe(`100 of ${(4093).toLocaleString()} rows loaded`);
        expect(countLine(false, 1, 1, 1)).toBe("1 row");
        expect(countLine(false, 22, 22, null)).toBe("22 rows");
    });
});

describe("drawing a layout", () => {
    it("offers an ENUM's own options to a mover, and nothing fixed for free text", async () => {
        const shape = await tablesSample().shape(SUPPORT.id, "tickets");
        const fixed = fixedValues(shape.columns);
        expect(fixed.get("status")).toEqual(["new", "waiting", "solved"]);
        expect(fixed.has("subject")).toBe(false);
        expect(fixedValues(undefined).size).toBe(0);
    });

    it("keeps a board's piles where they were when a card moves", () => {
        const first = pileOrder([], ["todo", "doing", "todo"]);
        expect(first).toEqual(["todo", "doing"]);
        expect(pileOrder(first, ["doing", "doing", "done"])).toEqual(["todo", "doing", "done"]);
    });

    it("lists what is left first in a checklist, then by when", () => {
        const rows = [{ id: "a", due: "2026-10-30", done: true }, { id: "b", due: "2026-11-02", done: false }, { id: "c", due: "2026-10-29", done: false }];
        const { rows: sorted, left } = checklistOrder(rows, "due", (row) => row.done === true);
        expect(sorted.map((row) => row.id)).toEqual(["c", "b", "a"]);
        expect(left).toBe(2);
    });

    it("groups a timeline by day, newest first", () => {
        const now = new Date(2026, 9, 24, 12);
        const days = timelineDays([{ id: "a", at: "2026-10-20" }, { id: "b", at: "2026-10-23" }, { id: "c", at: "2026-10-20" }], "at", now);
        expect(days.map((one) => [one.day, one.said, one.rows.map((row) => row.id)])).toEqual([["2026-10-23", "yesterday", ["b"]], ["2026-10-20", "4 days ago", ["a", "c"]]]);
    });

    it("plots a measure in date order and skips what is not a number", () => {
        const points = series([{ day: "2026-10-03", n: 2 }, { day: "2026-10-01", n: "1" }, { day: "2026-10-02", n: "x" }], "day", "n");
        expect(points.map((point) => [point.label, point.value])).toEqual([["2026-10-01", 1], ["2026-10-03", 2]]);
    });
});

describe("live rows", () => {
    const pages = (): RowPages => ({ pages: [{ items: [{ id: "1", title: "One" }, { id: "2", title: "Two" }], next: "p2" }, { items: [{ id: "3", title: "Three" }], next: null }], pageParams: [undefined, "p2"] });

    it("puts a new row at the top, merges a changed one where it is and drops a deleted one", () => {
        const { pages: next, moved } = applyChanges(pages(), "id", [
            { op: "insert", table: "tasks", record_id: "9", row: { id: "9", title: "Nine" } },
            { op: "update", table: "tasks", record_id: "3", row: { id: "3", title: "Three, again" } },
            { op: "delete", table: "tasks", record_id: "1", row: null },
        ]);
        expect(next?.pages.map((page) => page.items.map((row) => row.title))).toEqual([["Nine", "Two"], ["Three, again"]]);
        expect(moved).toBe(0);
    });

    it("does not add a made row twice when the socket's insert beat the create's own answer", () => {
        const first = applyChanges(pages(), "id", [{ op: "insert", table: "tasks", record_id: "9", row: { id: "9", title: "Nine" } }]);
        expect(first.moved).toBe(1);
        const made = withMade(first.pages, "id", { id: "9", title: "Nine", estimate: 2 });
        expect(made.added).toBe(false);
        expect(made.pages?.pages[0].items.filter((row) => row.id === "9")).toEqual([{ id: "9", title: "Nine", estimate: 2 }]);
        const fresh = withMade(pages(), "id", { id: "10", title: "Ten" });
        expect(fresh.added).toBe(true);
        expect(fresh.pages?.pages[0].items[0]).toEqual({ id: "10", title: "Ten" });
    });

    it("does not count a row twice when its insert arrives after the screen already added it", () => {
        const { pages: next, moved } = applyChanges(pages(), "id", [{ op: "insert", table: "tasks", record_id: "2", row: { id: "2", title: "Two, saved" } }]);
        expect(next?.pages[0].items.map((row) => row.title)).toEqual(["One", "Two, saved"]);
        expect(moved).toBe(0);
        expect(applyChanges(pages(), "id", [{ op: "delete", table: "tasks", record_id: "7", row: null }]).moved).toBe(0);
    });
});

describe("links between tables", () => {
    it("finds what a sample ticket points at and what points back at a customer", async () => {
        const source = tablesSample();
        const tickets = await source.shape(SUPPORT.id, "tickets");
        expect(outwardLinks(tickets)).toEqual([{ column: "customer_id", table: "customers", referencedColumn: "id" }]);
        const back = inwardLinks(await source.shapes(SUPPORT.id), "customers");
        expect(back).toEqual([{ table: "tickets", column: "customer_id", referencedColumn: "id" }]);
        const northwind = (await source.rows(SUPPORT.id, "customers")).items[0];
        const theirs = await source.referencing(SUPPORT.id, "tickets", "customer_id", String(northwind.id), 6);
        expect(theirs.map((row) => row.subject)).toEqual(["Order 4471 arrived broken", "Refund for the cracked lamp"]);
    });

    it("keeps a sample write for as long as the app is open", async () => {
        const source = tablesSample();
        const made = await source.create(LAUNCH.id, "launch_checklist", { task: "Thank the beta testers", done: false });
        await source.update(LAUNCH.id, "launch_checklist", String(made.id), { done: true });
        expect((await source.record(LAUNCH.id, "launch_checklist", String(made.id))).done).toBe(true);
        await source.remove(LAUNCH.id, "launch_checklist", String(made.id));
        await expect(source.record(LAUNCH.id, "launch_checklist", String(made.id))).rejects.toThrow();
    });
});

describe("feature flags", () => {
    it("reads the web's own site config", () => {
        expect(readSiteConfig('window.__LEMMA_SITE__={"features":"contacts","analyticsKey":"","deployment":"hosted"};')).toEqual({ features: "contacts", analyticsKey: "", deployment: "hosted" });
        expect(readSiteConfig("<html>not it</html>")).toBeNull();
    });

    it("reads PostHog's answer in either shape", () => {
        expect(flagsOn({ flags: { contacts: { key: "contacts", enabled: true }, other: { enabled: false } } })).toEqual(["contacts"]);
        expect(flagsOn({ featureFlags: { contacts: true, other: false } })).toEqual(["contacts"]);
        expect(flagsOn(null)).toEqual([]);
    });
});
