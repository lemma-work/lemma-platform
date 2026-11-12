import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
    JOIN_WORDS, castIcon, isNameConflict, joinState, kindSaid, setupSaid, setupSettled, sharedOpen, standInWords, startedLanding, takenSentence, workLine,
} from "../src/areas/more/begin/model";
import { shelfCardOf } from "../src/areas/more/begin/live";
import { beginSample, SAMPLE_SHELF } from "../src/areas/more/begin/sample";
import { shelfOf } from "@web/outcomes/model";
import type { OutcomeTemplateCard } from "lemma-sdk";

/** Starting a Lemma and asking to join one (PARITY.md, G). The web's own
 *  modules are held to the web; the phone's reading is `model.ts`. */

const mobile = fileURLToPath(new URL("..", import.meta.url));
describe("the shelf", () => {
    it("groups the server's templates by the work they do, in the order it ships them", () => {
        const works = shelfOf(SAMPLE_SHELF);
        expect(works.map((one) => one.label)).toEqual(["Support", "Sales", "Engineering", "Marketing", "Ops"]);
        expect(workLine(works[0])).toBe("Every customer hears back within a day.");
        expect(workLine(works[1])).toBe("Follow-ups · Partners");
        expect(works[1].places).toEqual(["HubSpot"]);
    });

    it("reads a template as the web reads it, and wears the face it showed", () => {
        const card = shelfCardOf({ id: "audit", name: "Audit", sentence: "The SOC 2 audit passes by {due}.", kind: "date", function: "ops", face: "ring", place: "Notion", brings: ["A table"], plan: { never: ["Fake evidence"] }, outcome: {} } as unknown as OutcomeTemplateCard);
        expect(card).toEqual({ id: "audit", name: "Audit", sentence: "The SOC 2 audit passes by {due}.", kind: "date", work: "ops", face: "ring", place: "Notion", brings: ["A table"], never: ["Fake evidence"] });
        expect(castIcon("ring")).toBe("/teammates/cast/ring.webp");
        expect(castIcon("nobody")).toBeNull();
        expect(castIcon(null)).toBeNull();
        expect(kindSaid("date")).toBe("A finish line, on a date you pick");
    });

    it("opens a shared link at the phone's own screen, a share on the web, and nothing else", () => {
        const origins = ["https://lemma.work"];
        expect(sharedOpen("github.com/acme/support-pod", origins)).toBe("/import/github/acme/support-pod");
        expect(sharedOpen("https://lemma.work/remix?source=abc", origins)).toBe("/remix?source=abc");
        expect(sharedOpen("https://lemma.work/s/tok123", origins)).toBe("/web?pod=&tail=%2Fs%2Ftok123&title=Shared%20with%20you");
        expect(sharedOpen("https://elsewhere.example/s/tok123", origins)).toBeNull();
        expect(sharedOpen("not a link", origins)).toBeNull();
    });
});

describe("starting one", () => {
    it("lands in the chat it sets itself up in, or a stand-in's in sample data", () => {
        expect(startedLanding({ id: "p1", name: "Support", conversationId: "c1", importId: null })).toBe("/chat/p1/c1");
        expect(startedLanding({ id: "p1", name: "Support", conversationId: "new", importId: null, standsIn: { id: "sample-support-1140", name: "Support" } })).toBe("/chat/sample-support-1140/new");
    });

    it("says what its template is bringing in, as it comes", () => {
        const steps = [{ kind: "TABLE", name: "conversations" }, { kind: "FILE", name: "skills/set-up/SKILL.md" }, { kind: "SCHEDULE", name: "friday" }];
        expect(setupSaid("Support", { status: "RUNNING", steps: [], error: null })).toBe("Bringing in its tables, skills and automations…");
        expect(setupSaid("Support", { status: "COMPLETED", steps, error: null })).toMatch(/^Brought in 1 table/);
        expect(setupSaid("Support", { status: "FAILED", steps, error: "No space" })).toBe("Couldn’t bring in everything: No space. Support starts with what is here.");
        expect(setupSettled("RUNNING")).toBe(false);
        expect(setupSettled("COMPLETED")).toBe(true);
    });

    it("says a name already here, and reads the server's refusal of one", () => {
        expect(takenSentence(" Support ")).toBe("There’s already a Lemma called Support here. Choose another name.");
        expect(isNameConflict(Object.assign(new Error("Conflict"), { statusCode: 409 }))).toBe(true);
        expect(isNameConflict(new Error("A pod named Support already exists in this organization"))).toBe(true);
        expect(isNameConflict(new Error("Network request failed"))).toBe(false);
    });
});

describe("asking to join", () => {
    const ask = (standing: "pending" | "approved" | "refused") => ({ id: "j1", podId: "p1", standing, name: "", email: "", askedAt: "" });

    it("tells the four states apart", () => {
        expect(joinState({ standing: null, asked: undefined })).toBe("closed");
        expect(joinState({ standing: ask("pending"), asked: undefined })).toBe("waiting");
        expect(joinState({ standing: ask("approved"), asked: undefined })).toBe("admitted");
        expect(joinState({ standing: null, asked: ask("approved") })).toBe("admitted");
        expect(joinState({ standing: ask("refused"), asked: undefined })).toBe("closed");
        expect(joinState({ standing: null, asked: undefined, askError: Object.assign(new Error("Not found"), { statusCode: 404 }) })).toBe("missing");
    });
});

describe("sample data", () => {
    it("starts from a template on a stand-in, and brings its template in step by step", async () => {
        const source = beginSample();
        expect((await source.templates()).length).toBe(7);
        const started = await source.start({ orgId: "sample-acme", template: "reviews", due: null });
        expect(started).toMatchObject({ name: "Reviews", conversationId: "new", importId: "sample-import-reviews", standsIn: { name: "Launch" } });
        expect((await source.start({ orgId: "sample-acme", name: "Invoices", sentence: "Every invoice is paid", kind: "week", due: null })).importId).toBeNull();
        const first = await source.setup(started.id, started.importId!);
        expect(first.status).toBe("RUNNING");
        expect(first.steps.length).toBeGreaterThan(0);
        expect(await source.needsModel("sample-acme")).toBe(false);
        expect(await source.needsModel("sample-acme")).toBe(true);
    }, 10_000);

    it("has an id for every state of asking to join", async () => {
        const source = beginSample();
        expect(await source.myJoinRequest("sample-closed")).toBeNull();
        expect((await source.askToJoin("sample-closed")).standing).toBe("pending");
        expect((await source.myJoinRequest("sample-waiting"))?.standing).toBe("pending");
        expect((await source.askToJoin("sample-open-door")).standing).toBe("approved");
        expect((await source.myJoinRequest("sample-books-986"))?.standing).toBe("approved");
        await expect(source.askToJoin("sample-missing")).rejects.toMatchObject({ statusCode: 404 });
    }, 10_000);
});

describe("copy", () => {
    const said = [
        ...Object.values(JOIN_WORDS).flatMap((one) => [one.title, one.body]),
        standInWords("Reviews", "Launch"),
        takenSentence("Support"),
        kindSaid("week"),
        kindSaid("date"),
        setupSaid("Support", { status: "AWAITING_CONFIRMATION", steps: [], error: null }),
    ];

    it("calls a Lemma a Lemma and people people, with no exclamation or em dash", () => {
        for (const line of said) {
            expect(line).not.toMatch(/\b(pod|pods|teammate|teammates|workspace|agent|bot|assistant|members?|users?)\b/i);
            expect(line).not.toMatch(/[!—]/);
        }
    });
});

describe("the screens", () => {
    const files = ["new-lemma.tsx", "setup-strip.tsx", "not-yours.tsx"];
    for (const file of files) {
        it(`${file} draws with no banned words, literal colours or bold`, () => {
            const screen = readFileSync(mobile + "src/areas/more/begin/" + file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
            const literals = [...screen.matchAll(/"([^"\n]*)"|`([^`\n]*)`|>([^<>{}\n]+)</g)].map((m) => m[1] ?? m[2] ?? m[3]).filter((one) => !/^\.\.?\//.test(one));
            for (const literal of literals) expect(literal).not.toMatch(/\bpods?\b|teammates?|\bworkspaces?\b|\bmembers\b|\busers?\b|—/i);
            expect(screen).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i);
            expect(screen).not.toMatch(/fontWeight|weight="bold"/);
        });
    }
});
