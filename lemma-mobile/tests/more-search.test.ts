import { readdirSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { peopleSample } from "../src/areas/admin/people/sample";
import { chatsSample } from "../src/areas/chats/sample";
import {
    askTarget,
    CATALOGUE,
    catalogueHits,
    destinationOf,
    docHits,
    failedLine,
    filesPerLemma,
    foundOf,
    functionChatFallback,
    functionHits,
    headingOf,
    joinAnd,
    lemmaCandidates,
    NOTHING_MATCHES,
    pickRecordTables,
    placeholderOf,
    quietLine,
    recordHits,
    rowMatches,
    scheduleHits,
    secondLine,
    serverQueryOf,
    skippedLine,
    whenOf,
    type Found,
    type Opening,
} from "../src/areas/more/search/model";
import { groupByKind, highlight, KINDS, rank } from "@web/search/matching";
import { MAX_RECORD_TABLES, recordSearchSql, recordSourcesFrom, type RecordSource } from "@web/search/sources";
import { tablesSample } from "../src/areas/tables/sample";
import type { StandingJob } from "../src/areas/work/source";
import { workSample } from "../src/areas/work/sample";
import { SAMPLE_LEMMAS, sampleSource } from "../src/data/sample";

/** Search (PARITY.md, G): the web's matching and readers, copied and held to
 *  the web; the phone's reading on top of them; and the sample world, where
 *  every hit has to open a screen that exists. */

const mobile = fileURLToPath(new URL("..", import.meta.url));
const app = mobile + "src/app/";

const [SUPPORT, LAUNCH, , , BOOKS] = SAMPLE_LEMMAS;

/* ── the screens a hit opens ─────────────────────────────────────── */

/** The route file Expo Router would render for an app path: groups like
 *  `(in)` and `(tabs)` are transparent, a literal segment beats `[param]`. */
function screenFor(href: string): string | null {
    const segments = href.split("?")[0].split("/").filter(Boolean).map(decodeURIComponent);
    return match(app, segments);
}

function match(dir: string, segments: string[]): string | null {
    const entries = readdirSync(dir);
    const ordered = [...entries.filter((one) => !one.startsWith("[")), ...entries.filter((one) => one.startsWith("["))];
    for (const entry of ordered) {
        const path = dir + entry;
        if (statSync(path).isDirectory()) {
            if (/^\(.*\)$/.test(entry)) {
                const found = match(path + "/", segments);
                if (found) return found;
            } else if (segments.length > 0 && (entry === segments[0] || /^\[[^.]+\]$/.test(entry))) {
                const found = match(path + "/", segments.slice(1));
                if (found) return found;
            }
            continue;
        }
        if (!entry.endsWith(".tsx") || entry.startsWith("_") || entry.startsWith("+")) continue;
        const base = entry.slice(0, -4);
        if (segments.length === 0 && base === "index") return path.slice(app.length);
        if (segments.length === 1 && (base === segments[0] || /^\[[^.]+\]$/.test(base))) return path.slice(app.length);
    }
    return null;
}

const P = "sample-support-1140";
const at = (open: Opening): Found => ({ podId: P, open });
const href = (open: Opening) => {
    const where = destinationOf(at(open));
    return "href" in where ? where.href : null;
};

describe("where a hit opens", () => {
    it.each<[Opening, string, string]>([
        [{ kind: "lemma" }, `/lemma/${P}`, "(in)/lemma/[podId]/index.tsx"],
        [{ kind: "chat", id: "support-refund" }, `/chat/${P}/support-refund`, "(in)/chat/[podId]/[id].tsx"],
        [{ kind: "file", path: "/pages/Weekly update.md" }, `/lemma/${P}/file?path=%2Fpages%2FWeekly%20update.md`, "(in)/lemma/[podId]/file.tsx"],
        [{ kind: "table", name: "tickets" }, `/lemma/${P}/table/tickets`, "(in)/lemma/[podId]/table/[name].tsx"],
        [{ kind: "record", table: "tickets", id: "tickets-1" }, `/lemma/${P}/record/tickets/tickets-1`, "(in)/lemma/[podId]/record/[table]/[id].tsx"],
        [{ kind: "record", table: "tickets", id: null }, `/lemma/${P}/table/tickets`, "(in)/lemma/[podId]/table/[name].tsx"],
        [{ kind: "app", name: "refund-desk" }, `/app/${P}/refund-desk`, "(in)/app/[podId]/[name].tsx"],
        [{ kind: "agent", name: "reply_writer" }, `/lemma/${P}/agent/reply_writer`, "(in)/lemma/[podId]/agent/[name].tsx"],
        [{ kind: "workflow", name: "chase_unanswered_tickets" }, `/lemma/${P}/workflow/chase_unanswered_tickets`, "(in)/lemma/[podId]/workflow/[name].tsx"],
        [{ kind: "schedule", id: "sched-digest" }, `/lemma/${P}/schedule/sched-digest`, "(in)/lemma/[podId]/schedule/[id].tsx"],
        [{ kind: "person" }, `/lemma/${P}/section/people`, "(in)/lemma/[podId]/section/[name].tsx"],
    ])("%o opens %s, a screen the phone has", (open, expected, screen) => {
        const went = href(open);
        expect(went).toBe(expected);
        expect(screenFor(went!)).toBe(screen);
    });

    it("lands a person on the People section, which a Lemma has", () => {
        expect(readFileSync(mobile + "src/ui/lemmas/sections.tsx", "utf8")).toContain('{ name: "people", label: "People" }');
    });

    it("sends a function to its chat, or a new chat with words in the box", () => {
        expect(destinationOf(at({ kind: "function", name: "issue_refund", label: "Issue refund" }))).toEqual({ discuss: { podId: P, name: "issue_refund", label: "Issue refund" } });
        const fallback = functionChatFallback(P, "Issue refund");
        expect(fallback).toBe(`/chat/${P}/new?fill=About%20the%20issue%20refund%20function%3A%20`);
        expect(screenFor(fallback)).toBe("(in)/chat/[podId]/[id].tsx");
        expect(readFileSync(app + "(in)/chat/[podId]/[id].tsx", "utf8")).toMatch(/fill\?: string/);
    });
});

/* ── the readers ─────────────────────────────────────────────────── */

describe("reading each list as hits in one Lemma", () => {
    it("keeps ids unique across Lemmas and says where each opens", () => {
        const one = catalogueHits(SUPPORT, "tables", { items: [{ name: "tickets", columns: 9, updatedAt: null, perUser: false, access: "space" }] });
        const two = catalogueHits(LAUNCH, "tables", { items: [{ name: "tickets", columns: 4, updatedAt: null, perUser: false, access: "space" }] });
        expect(one[0].id).not.toBe(two[0].id);
        expect(one[0]).toMatchObject({ kind: "table", title: "Tickets", subtitle: "9 columns", haystack: "tickets" });
        expect(foundOf(two[0])).toEqual({ podId: LAUNCH.id, open: { kind: "table", name: "tickets" } });
    });

    it("names functions and apps the phone's way, found by their stored names too, and drops what has no name", () => {
        const fns = functionHits(P, { items: [{ id: "f1", name: "issue_refund", description: "Refunds an order." }, { id: "f2" }] });
        expect(fns).toHaveLength(1);
        expect(fns[0]).toMatchObject({ title: "Issue refund", subtitle: "Refunds an order.", haystack: "issue_refund" });
        expect(foundOf(fns[0])?.open).toEqual({ kind: "function", name: "issue_refund", label: "Issue refund" });
        const apps = catalogueHits(SUPPORT, "apps", { items: [{ name: "refund-desk" }] });
        expect(apps[0]).toMatchObject({ kind: "app", title: "Refund Desk" });
        expect(foundOf(apps[0])?.open).toEqual({ kind: "app", name: "refund-desk" });
    });

    it("leaves out the schedules a workflow made for its own waits, and finds one by what fires it", () => {
        const job = (id: string, internal: boolean): StandingJob => ({
            id, name: "morning-digest", title: "Morning digest", kind: "TIME", trigger: "Every weekday at 09:00", triggerLiteral: "0 9 * * 1-5",
            target: { kind: "agent", name: "POD_DEFAULT", label: "Support" }, instruction: "", filter: "", active: true, pausedByFailures: false, internal,
        } as unknown as StandingJob);
        const hits = scheduleHits(P, [job("s1", false), job("s2", true)]);
        expect(hits).toHaveLength(1);
        expect(hits[0]).toMatchObject({ title: "Morning digest", subtitle: "Every weekday at 09:00" });
        expect(rank(hits, "weekday")).toHaveLength(1);
        expect(rank(hits, "0 9 *")).toHaveLength(1);
    });

    it("makes one hit of a file the server answered in several passages, called as the files screens call it", () => {
        const raw = { items: [
            { path: "/pages/Weekly update.md", content: "Refunds this week", score: 0.9 },
            { path: "/pages/Weekly update.md", content: "More refunds", score: 0.5 },
            { path: "/Notes/Welcome.md", content: "Check the refunds over $200", score: 0.4 },
        ] };
        const docs = docHits(P, raw);
        expect(docs.map((one) => one.title)).toEqual(["Weekly update", "Welcome"]);
        expect(docs[0].subtitle).toBe("/pages/Weekly update.md");
        /* Found inside, not in the name: kept, below what matched by name. */
        const ranked = rank([...docs, ...lemmaCandidates([{ id: "x", name: "Refunds", iconUrl: null, description: null }])], "refunds");
        expect(ranked.map((one) => one.title)).toEqual(["Refunds", "Weekly update", "Welcome"]);
    });

    it("finds chats a person started, not the runs a workflow or a schedule did", () => {
        const chats = catalogueHits(SUPPORT, "chats", { items: [
            { id: "c1", title: "Chase the refund", activityAt: null, origin: { kind: "chat", label: "Chat" }, waitingOnYou: false, boundTo: null },
            { id: "c2", title: "Workflow run: chase_unanswered_tickets", activityAt: null, origin: { kind: "workflow", label: "Workflow" }, waitingOnYou: false, boundTo: null },
            { id: "c3", title: "Morning digest chase", activityAt: null, origin: { kind: "schedule", label: "Schedule" }, waitingOnYou: true, boundTo: null },
        ] });
        expect(chats.map((one) => one.title)).toEqual(["Chase the refund", "Morning digest chase"]);
    });

    it("keeps a chat's time out of the matching", () => {
        const chats = catalogueHits(SUPPORT, "chats", { items: [{ id: "c1", title: "Tone for replies", activityAt: "2026-10-19T10:00:00.000Z" }] });
        expect(rank(chats, "mon")).toHaveLength(0);
        expect(whenOf(foundOf(chats[0]), Date.parse("2026-10-24T10:00:00.000Z"))).toBe("Mon");
    });
});

/* ── the sample world: every hit opens something that is there ───── */

describe("sample data", () => {
    it("every list a Lemma holds turns into hits that open screens which exist, on things that exist", async () => {
        const work = workSample();
        const chats = chatsSample();
        const people = peopleSample();
        const tables = tablesSample();
        const apps = await sampleSource().apps("sample-acme", SAMPLE_LEMMAS);
        let opened = 0;
        for (const lemma of SAMPLE_LEMMAS) {
            const lists: Record<(typeof CATALOGUE)[number], unknown> = {
                chats: await chats.conversations(lemma.id, null, null),
                agents: await work.agents(lemma.id),
                workflows: await work.workflows(lemma.id),
                functions: { items: [] },
                apps: { items: apps.filter((one) => one.podId === lemma.id).map((one) => ({ id: one.id, name: one.name })) },
                people: await people.members(lemma.id),
                schedules: await work.schedules(lemma.id),
                tables: await tables.tables(lemma.id),
            };
            for (const part of CATALOGUE) {
                for (const hit of catalogueHits(lemma, part, lists[part])) {
                    const found = foundOf(hit)!;
                    expect(found.podId).toBe(lemma.id);
                    const where = destinationOf(found);
                    expect("href" in where && screenFor(where.href)).toBeTruthy();
                    const { open } = found;
                    if (open.kind === "agent") await expect(work.agent(lemma.id, open.name)).resolves.toBeTruthy();
                    if (open.kind === "workflow") await expect(work.workflow(lemma.id, open.name)).resolves.toBeTruthy();
                    if (open.kind === "table") await expect(tables.shape(lemma.id, open.name)).resolves.toBeTruthy();
                    if (open.kind === "app") expect(apps.some((one) => one.podId === lemma.id && one.name === open.name)).toBe(true);
                    opened += 1;
                }
            }
        }
        expect(opened).toBeGreaterThan(40);
    });

    it("finds rows by any text column, and each opens its own row", async () => {
        const tables = tablesSample();
        const { sources } = recordSourcesFrom(await tables.shapes(SUPPORT.id), Infinity);
        const tickets = sources.find((one) => one.tableName === "tickets")!;
        expect(tickets.searchFields).toEqual(expect.arrayContaining(["subject", "status", "email"]));
        const rows = (await tables.rows(SUPPORT.id, "tickets")).items.filter((row) => rowMatches(row, tickets.searchFields, "globex"));
        const hits = recordHits(SUPPORT.id, tickets, rows);
        expect(hits.length).toBeGreaterThan(0);
        /* Found by the email column: the title does not say it, the row is kept. */
        expect(rank(hits, "globex")).toHaveLength(hits.length);
        for (const hit of hits) {
            const open = foundOf(hit)!.open;
            expect(open.kind).toBe("record");
            if (open.kind === "record") await expect(tables.record(SUPPORT.id, "tickets", open.id!)).resolves.toBeTruthy();
            expect(hit.subtitle).toBe("Tickets");
        }
    });

    it("searches every sample table with words in it, and none without", async () => {
        const tables = tablesSample();
        const perLemma = await Promise.all(SAMPLE_LEMMAS.map(async (lemma) => ({ podId: lemma.id, sources: recordSourcesFrom(await tables.shapes(lemma.id), Infinity).sources })));
        const names = perLemma.flatMap((one) => one.sources.map((source) => source.tableName));
        expect(names).not.toContain("daily_revenue");
        expect(pickRecordTables(perLemma)).toMatchObject({ skipped: 0 });
        expect(names.length).toBeLessThanOrEqual(MAX_RECORD_TABLES);
    });
});

/* ── ranking, grouping, the bound ────────────────────────────────── */

describe("the list", () => {
    const sources = (podId: string, names: string[]): { podId: string; sources: RecordSource[] } => ({
        podId,
        sources: names.map((name) => ({ key: "table:" + name, tableName: name, label: name, searchFields: ["title"], displayField: "title", limit: 5 })),
    });

    it("shares the record bound across Lemmas a table at a time, and counts what it left out", () => {
        const { picked, skipped } = pickRecordTables([sources("a", ["a1", "a2", "a3"]), sources("b", ["b1"]), sources("c", ["c1", "c2"])], 4);
        expect(picked.map((one) => one.source.tableName)).toEqual(["a1", "b1", "c1", "a2"]);
        expect(skipped).toBe(2);
        expect(pickRecordTables([], 8)).toEqual({ picked: [], skipped: 0 });
        expect(pickRecordTables([sources("a", ["a1"])])).toMatchObject({ skipped: 0 });
    });

    it("heads each group in the phone's words, a row's group by its table", () => {
        const headings = KINDS.map((kind) => headingOf({ kind, label: "x" }));
        expect(headings).toEqual(["Lemmas", "Chats", "Files", "x", "Apps", "Agents", "Workflows", "Functions", "Tables", "People", "Schedules"]);
        expect(headingOf({ kind: "record", label: "Record" })).toBe("Records");
    });

    it("names the Lemma under each hit when every Lemma is searched, and never repeats the heading", () => {
        const record = recordHits(SUPPORT.id, sources(SUPPORT.id, ["tickets"]).sources[0], [{ id: "t1", title: "Order 4471 arrived broken" }]);
        const doc = docHits(SUPPORT.id, { items: [{ path: "/pages/Plan.md", content: "", score: 1 }] });
        const groups = [...groupByKind(rank(record, "order")), ...groupByKind(rank(doc, "plan")), ...groupByKind(rank(lemmaCandidates([SUPPORT]), "sup"))];
        const of = (kind: string) => groups.find((one) => one.kind === kind)!;
        expect(secondLine(of("record").hits[0], of("record"), "Support")).toBe("Support");
        expect(secondLine(of("record").hits[0], of("record"), null)).toBeNull();
        expect(secondLine(of("doc").hits[0], of("doc"), "Support")).toBe("Support · /pages/Plan.md");
        expect(secondLine(of("doc").hits[0], of("doc"), null)).toBe("/pages/Plan.md");
        expect(secondLine(of("teammate").hits[0], of("teammate"), "Support")).toBe("Answers customers.");
    });

    it("marks the typed letters for medium weight, nothing else", () => {
        const [hit] = rank(catalogueHits(SUPPORT, "apps", { items: [{ name: "ticket-queue" }] }), "que");
        expect(highlight(hit.title, hit.ranges)).toEqual([{ text: "Ticket ", hit: false }, { text: "Que", hit: true }, { text: "ue", hit: false }]); // typos: ignore
    });

    it("asks the server only from two letters, and fewer files each when every Lemma is asked", () => {
        expect(serverQueryOf(" a ")).toBe("");
        expect(serverQueryOf(" re ")).toBe("re");
        expect(filesPerLemma(true)).toBe(12);
        expect(filesPerLemma(false)).toBeLessThan(12);
    });

    it("asks the Lemma searched, else the one picked, else the first", () => {
        expect(askTarget(LAUNCH.id, BOOKS.id, SAMPLE_LEMMAS)?.name).toBe("Launch");
        expect(askTarget(null, BOOKS.id, SAMPLE_LEMMAS)?.name).toBe("Books");
        expect(askTarget(null, null, SAMPLE_LEMMAS)?.name).toBe("Support");
        expect(askTarget(null, null, [])).toBeNull();
    });

    it("escapes what is typed before it reaches a table", () => {
        const sql = recordSearchSql(sources("a", ["tickets"]).sources[0], "50%_o'k");
        expect(sql).toBe(`SELECT * FROM "tickets" WHERE "title"::text ILIKE '%50\\%\\_o''k%' ESCAPE '\\' LIMIT 5`);
    });
});

/* ── what it says ────────────────────────────────────────────────── */

describe("what search says", () => {
    const name = (id: string) => SAMPLE_LEMMAS.find((one) => one.id === id)?.name ?? "a Lemma";

    it("says what could not be reached, Lemma by Lemma, never quietly less", () => {
        expect(failedLine([], name, false)).toBeNull();
        expect(failedLine([{ part: "files", podId: LAUNCH.id }, { part: "files", podId: BOOKS.id }, { part: "people", podId: SUPPORT.id }], name, false))
            .toBe("Couldn’t reach files in Launch and Books; people in Support.");
        expect(failedLine([{ part: "people", podId: SUPPORT.id }, { part: "files", podId: SUPPORT.id }], name, true)).toBe("Couldn’t reach files and people.");
        expect(failedLine([{ part: "lemmas", podId: null }], name, false)).toBe("Couldn’t reach your Lemmas.");
        expect(joinAnd(["A", "B", "C"])).toBe("A, B and C");
    });

    it("says the record bound where it was applied", () => {
        expect(skippedLine(0)).toBeNull();
        expect(skippedLine(1)).toBe("1 more table not searched.");
        expect(skippedLine(3)).toBe("3 more tables not searched.");
    });

    it("says every line the phone's way: Lemmas and people, no exclamations, no em dashes", () => {
        const lines = [
            NOTHING_MATCHES,
            quietLine(null),
            quietLine("Support"),
            placeholderOf(null),
            placeholderOf("Support"),
            skippedLine(2)!,
            failedLine([{ part: "records", podId: SUPPORT.id }, { part: "lemmas", podId: null }], name, false)!,
            ...KINDS.map((kind) => headingOf({ kind, label: "Tickets" })),
            decodeURIComponent(functionChatFallback(P, "Issue refund").split("fill=")[1]),
        ];
        for (const line of lines) {
            expect(line).not.toMatch(/\bpods?\b|teammates?|\bspaces?\b|\bmembers?\b|\busers?\b|\bworkspace\b|!|—/i);
        }
    });

    it("draws its screen with no banned words or literal colours", () => {
        const screen = readFileSync(mobile + "src/areas/more/search/screens.tsx", "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
        const literals = [...screen.matchAll(/"([^"\n]*)"|`([^`\n]*)`|>([^<>{}\n]+)</g)].map((m) => m[1] ?? m[2] ?? m[3])
            /* Import paths and the web's kind names (`"teammate"`) are code, not copy. */
            .filter((one) => !/^\.\.?\//.test(one) && !(KINDS as readonly string[]).includes(one));
        for (const literal of literals) expect(literal).not.toMatch(/\bpods?\b|teammates?|—/i);
        expect(screen).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i);
        expect(screen).not.toMatch(/fontWeight|weight="bold"/);
    });
});
