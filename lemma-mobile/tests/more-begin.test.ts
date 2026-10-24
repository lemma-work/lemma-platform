import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { variantFor, variantSeed } from "../src/areas/admin/about/faces";
import {
    JOIN_WORDS, STARTERS, blankStart, characterOf, firstTasks, freeName, isNameConflict, joinState, kindOfStarter, nameTaken, nextFor,
    ownName, ownStart, ownedLanding, ownsLine, readyWords, sameStart, setupAsk, standInWords, startPlan, stepState, stepsFor, takenSentence,
} from "../src/areas/more/begin/model";
import { beginSample } from "../src/areas/more/begin/sample";
import { characterForSeed } from "@web/shell/cast";
import { EXPLORING_OPENERS } from "@web/data/hires";
import { parsePlan } from "../src/areas/work/web/plan";

/** Starting a Lemma and asking to join one (PARITY.md, G). The web's own
 *  modules are held to the web; the phone's reading is `model.ts`. */

const mobile = fileURLToPath(new URL("..", import.meta.url));
describe("what a new Lemma could own", () => {
    it("offers the /o frame's six starters, each said as what it owns", () => {
        expect(STARTERS.map((one) => one.name)).toEqual(["Reviews", "Renewals", "Onboarding", "Support", "Close", "Hiring"]);
        expect(ownsLine(STARTERS[0].outcome)).toBe("Owns: every pull request is reviewed the same day.");
        expect(ownsLine("Every invoice gets a second approval.")).toBe("Owns: every invoice gets a second approval.");
        expect(STARTERS.map(kindOfStarter)).toEqual(["week", "week", "week", "week", null, "date"]);
    });

    it("takes a name not already taken, and names an outcome said in its own words", () => {
        expect(freeName("Support", new Set(["launch"]))).toBe("Support");
        expect(freeName("Support", new Set(["support", "support 2"]))).toBe("Support 3");
        expect(ownName("Every invoice over $10,000 gets a second approval")).toBe("Invoice");
        expect(ownName("")).toBe("Lemma");
    });

    it("writes the outcome as its plan, and asks it to set itself up with the /o words", () => {
        const plan = parsePlan(startPlan("Every customer hears back within a day", "week"));
        expect(plan.outcome).toBe("Every customer hears back within a day");
        expect(plan.kind).toBe("week");
        expect(plan.sections.must.map((line) => line.text)).toEqual(["Every customer hears back within a day"]);
        const ask = setupAsk("Every customer hears back within a day");
        expect(ask.startsWith("Set yourself up to own this: \"Every customer hears back within a day\". Look at what is already connected")).toBe(true);
        expect(ask).toContain("then write the plan for what you own to /plan.md");
    });
});

describe("starting one", () => {
    it("lists only the steps that really happen", () => {
        expect(stepsFor(ownStart("Support 2", "Every customer hears back", "week")).map((step) => step.id)).toEqual(["made", "face", "plan", "ask"]);
        expect(stepsFor(blankStart("Ops", "")).map((step) => step.id)).toEqual(["made", "face"]);
        expect(stepsFor(blankStart("Ops", ""))[0].label).toBe("Starting Ops");
    });

    it("ticks a step once it finished, turns the next, and stops where a failure left it", () => {
        const steps = stepsFor(ownStart("Support", "x", "week"));
        expect(stepState("made", steps, ["made"], false)).toBe("done");
        expect(stepState("face", steps, ["made"], false)).toBe("running");
        expect(stepState("plan", steps, ["made"], false)).toBe("waiting");
        expect(stepState("face", steps, ["made"], true)).toBe("stopped");
    });

    it("knows a retry of the same start from a different start", () => {
        const one = blankStart("Ops", "Watches the inbox", "n1");
        expect(sameStart(one, { ...one })).toBe(true);
        expect(sameStart(one, blankStart("Ops", "Watches the inbox", "n2"))).toBe(false);
        expect(sameStart(null, one)).toBe(false);
        expect(sameStart(ownStart("Support", "a", "week"), ownStart("Support", "b", "week"))).toBe(false);
    });

    it("deals a blank start a face of its own, and finds the variant of the new id that draws it", () => {
        const start = blankStart("Ops", "", "abc");
        expect(characterOf(start)).toBe(characterForSeed("blank/abc"));
        const variant = variantFor("pod-123", characterOf(start), characterForSeed);
        expect(variant).not.toBeNull();
        expect(characterForSeed(variantSeed("pod-123", variant!))).toBe(characterOf(start));
    });

    it("says a name already here, and reads the server's refusal of one", () => {
        expect(nameTaken(" support ", ["Support", "Launch"])).toBe(true);
        expect(nameTaken("", ["Support"])).toBe(false);
        expect(takenSentence("Support")).toBe("There’s already a Lemma called Support here. Choose another name.");
        expect(isNameConflict(Object.assign(new Error("Conflict"), { statusCode: 409 }))).toBe(true);
        expect(isNameConflict(new Error("Pod with name 'Support' already exists in this organization"))).toBe(true);
        expect(isNameConflict(new Error("Network request failed"))).toBe(false);
    });
});

describe("the blank start's ending", () => {
    it("hands the job back as the first task, or offers ways of looking around", () => {
        expect(firstTasks("watches the inbox")).toEqual(["Watches the inbox."]);
        expect(firstTasks("Answer the refunds?")).toEqual(["Answer the refunds?"]);
        expect(firstTasks("")).toEqual(EXPLORING_OPENERS);
    });

    it("claims ready only when it can reply", () => {
        expect(readyWords("Ops", "Watches the inbox", false)).toEqual({ heading: "Ops is ready", line: "Share what it needs to know for its first task.", tasks: "Or give Ops a first task" });
        expect(readyWords("Ops", "", false).tasks).toBe("Or ask Ops something");
        expect(readyWords("Ops", "", true)).toMatchObject({ heading: "Ops needs an AI model before it can reply.", line: null });
    });

    it("opens the phone's own screens", () => {
        expect(nextFor.open("p1")).toBe("/lemma/p1");
        expect(nextFor.reach("p1")).toBe("/lemma/p1/settings/channels");
        expect(nextFor.people("p1")).toBe("/lemma/p1/settings/people/add");
        expect(nextFor.models()).toBe("/settings/models");
        expect(nextFor.task("p1", "Hello there.")).toBe("/chat/p1/new?fill=Hello%20there.");
    });

    it("lands an owned start in its setup chat, its page, or a stand-in's box in sample data", () => {
        expect(ownedLanding({ id: "p1" }, "c1", "Set up")).toBe("/chat/p1/c1");
        expect(ownedLanding({ id: "p1" }, null, "Set up")).toBe("/lemma/p1");
        expect(ownedLanding({ id: "p1", standsIn: { id: "sample-support-1140" } }, "new", "Set up")).toBe("/chat/sample-support-1140/new?fill=Set%20up");
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
    it("walks a start in memory and lands on a stand-in", async () => {
        const source = beginSample();
        expect((await source.create("sample-acme", "Support 2", "x")).standsIn).toEqual({ id: "sample-support-1140", name: "Support" });
        expect((await source.create("sample-acme", "Reviews", "x")).standsIn?.name).toBe("Launch");
        expect((await source.create("sample-acme", "Ops", "")).standsIn?.id).toBe("sample-support-1140");
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
        ...stepsFor(ownStart("Support", "x", "week")).map((step) => step.label),
        ...Object.values(readyWords("Ops", "", true)).filter((line): line is string => Boolean(line)),
        ...Object.values(readyWords("Ops", "Watches the inbox", false)).filter((line): line is string => Boolean(line)),
        standInWords("Reviews", "Launch", true),
        standInWords("Ops", "Support", false),
        takenSentence("Support"),
        ...STARTERS.map((one) => ownsLine(one.outcome)),
    ];

    it("calls a Lemma a Lemma and people people, with no exclamation or em dash", () => {
        for (const line of said) {
            expect(line).not.toMatch(/\b(pod|pods|teammate|teammates|workspace|agent|bot|assistant|members?|users?)\b/i);
            expect(line).not.toMatch(/[!—]/);
        }
    });
});

describe("the screens", () => {
    const files = ["new-lemma.tsx", "making.tsx", "met.tsx", "not-yours.tsx"];
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
