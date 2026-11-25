import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { peopleSample } from "../src/areas/admin/people/sample";
import { addressOf, linkRow, liveLinksLine, onlyReader, opensForAnyone, shareTitle, whoCanOpen } from "../src/areas/more/share/model";
import type { Reach, SharedThing, ShareSubject } from "../src/areas/more/share/source";
import { tablesSample } from "../src/areas/tables/sample";
import { SAMPLE_LEMMAS } from "../src/data/sample";

/** Share (PARITY.md, G): who can open a Lemma, a file, a table or an app,
 *  and the address the sheet hands out, said the phone's way. */

const mobile = fileURLToPath(new URL("..", import.meta.url));
const [SUPPORT, , , , BOOKS] = SAMPLE_LEMMAS;
const WEB = "https://lemma.work";

const file: SharedThing = { kind: "file", path: "/pages/Plan.md", label: "Plan" };
const table: SharedThing = { kind: "table", name: "tickets", label: "Tickets" };
const app: SharedThing = { kind: "app", name: "refund-desk", label: "Refund desk" };
const reach = (visibility: string | null, rowsOwn = false): Reach => ({ visibility, rowsOwn });

describe("who can open it", () => {
    it.each<[SharedThing, Reach, string]>([
        [file, reach("POD"), "Everyone in Support can open this."],
        [file, reach(null), "Everyone in Support can open this."],
        [file, reach("public"), "Anyone with a Lemma account can open this, and people outside Support get answers from it in its groups."],
        [file, reach("PERSONAL"), "Only you can open this. It is in your private files."],
        [file, reach("RESTRICTED"), "Only the people it was shared with can open this."],
        [table, reach("POD", true), "Everyone in Support can open this, and each sees only their own rows."],
        [table, reach("POD"), "Everyone in Support can open this."],
        [table, reach("PERSONAL"), "Only you can open this."],
        [table, reach("RESTRICTED", true), "Only the people it was shared with can open this."],
        [app, reach("PUBLIC"), "Anyone with its address can open this, without signing in."],
        [app, reach("POD"), "Everyone in Support can open this."],
        [app, reach("PERSONAL"), "Only the person who made it can open this."],
        [app, reach("RESTRICTED"), "Only the people it was shared with can open this."],
    ])("%o with %o: %s", (thing, said, line) => {
        expect(whoCanOpen(thing, said, "Support")).toBe(line);
    });

    it("treats only an app's PUBLIC as the open internet", () => {
        expect(opensForAnyone(app, reach("PUBLIC"))).toBe(true);
        expect(opensForAnyone(file, reach("PUBLIC"))).toBe(false);
        expect(opensForAnyone(app, reach("POD"))).toBe(false);
        expect(opensForAnyone(app, null)).toBe(false);
    });
});

describe("the address it hands out", () => {
    it.each<[ShareSubject, { fileUrl?: string | null; appUrl?: string | null }, string | null]>([
        [{ kind: "lemma" }, {}, `${WEB}/t/p%201`],
        [table, {}, `${WEB}/t/p%201/table/tickets`],
        [file, { fileUrl: `${WEB}/pod/p/files?file=x` }, `${WEB}/pod/p/files?file=x`],
        [file, {}, null],
        [app, { appUrl: "https://refund-desk.apps.example.com" }, "https://refund-desk.apps.example.com"],
        [app, { appUrl: null }, `${WEB}/t/p%201/app/refund-desk`],
    ])("%o: %s", (subject, extra, expected) => {
        expect(addressOf(subject, { web: WEB + "/", podId: "p 1", ...extra })).toBe(expected);
    });

    it("says who the address works for", () => {
        expect(linkRow({ kind: "lemma" }, null, "Support", false)).toEqual({ title: "People in Support", line: "Anyone else is asked to join first.", anyone: false });
        expect(linkRow(app, reach("PUBLIC"), "Support", true)).toEqual({ title: "Anyone with the address", line: "The app opens without signing in.", anyone: true });
        /* Sent at the web's page for it, a public app still asks who is asking. */
        expect(linkRow(app, reach("PUBLIC"), "Support", false)).toEqual({ title: "People in Support", line: "This link opens it in Lemma, where anyone else is asked to join first.", anyone: false });
        expect(linkRow(file, reach("PUBLIC"), "Support", false).anyone).toBe(false);
    });

    it("counts a file's live public links", () => {
        expect(liveLinksLine(0)).toBe("A read-only link that expires.");
        expect(liveLinksLine(1)).toBe("One link is live.");
        expect(liveLinksLine(3)).toBe("3 links are live.");
    });

    it("is called by what is shared", () => {
        expect(shareTitle({ kind: "lemma" }, "Support")).toBe("Share Support");
        expect(shareTitle(file, "Support")).toBe("Share Plan");
    });
});

describe("sample data", () => {
    it("has a Lemma with people in it and one with only the reader", async () => {
        const people = peopleSample();
        const me = { email: "ada@acme.example", id: "me" };
        const support = await people.members(SUPPORT.id);
        expect(support.length).toBeGreaterThan(1);
        expect(onlyReader(support, me)).toBe(false);
        expect(onlyReader(await people.members(BOOKS.id), me)).toBe(true);
        expect(onlyReader([], me)).toBe(true);
    });

    it("reads a table's reach from the tables sample, so the Tables tab agrees", async () => {
        const tables = tablesSample();
        expect((await tables.shape(BOOKS.id, "invoices")).visibility).toBe("RESTRICTED");
        expect((await tables.shape(SUPPORT.id, "tickets")).visibility).toBe("POD");
    });
});

describe("what the sheet says", () => {
    it("says it the phone's way: Lemma and people, no exclamations, no em dashes", () => {
        const lines = [
            ...[file, table, app].flatMap((thing) => ["PUBLIC", "POD", "PERSONAL", "RESTRICTED", null].flatMap((visibility) => [whoCanOpen(thing, reach(visibility), "Support"), whoCanOpen(thing, reach(visibility, true), "Support")])),
            ...Object.values(linkRow(app, reach("PUBLIC"), "Support", true)).filter((one): one is string => typeof one === "string"),
            ...Object.values(linkRow(file, null, "Support", false)).filter((one): one is string => typeof one === "string"),
            liveLinksLine(0), liveLinksLine(1), liveLinksLine(2),
        ];
        for (const line of lines) expect(line).not.toMatch(/\bpods?\b|teammates?|\bspaces?\b|\bmembers?\b|\busers?\b|\bworkspace\b|!|—/i);
    });

    it("draws the sheet with no banned words, literal colours or bold", () => {
        const sheet = readFileSync(mobile + "src/areas/more/share/sheet.tsx", "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
        const literals = [...sheet.matchAll(/"([^"\n]*)"|`([^`\n]*)`|>([^<>{}\n]+)</g)].map((m) => m[1] ?? m[2] ?? m[3]).filter((one) => !/^\.\.?\//.test(one));
        for (const literal of literals) expect(literal).not.toMatch(/\bpods?\b|teammates?|\bmembers?\b|—|!$/i);
        expect(sheet).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i);
        expect(sheet).not.toMatch(/fontWeight|weight="bold"/);
    });
});
