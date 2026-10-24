import { describe, expect, it } from "vitest";
import { identityIcon, startedLine, variantFor, variantSeed } from "../src/areas/admin/about/faces";
import { isConnectReturn, outcomeNote, outcomeOf } from "../src/areas/admin/connect/outcome";
import { LEMMA_PARTS, SETTINGS, lemmaPart, offeredSettings, settingsEntry } from "../src/areas/admin/sections";
import { namesTeammate } from "@web/space/confirm-name";
import { count, firstFew, isForbidden, isMissing, problemOf, statusOf } from "../src/areas/admin/words";

/** Settings (PARITY.md, F): the pure parts every settings screen leans on,
 *  and the web's modules copied under `src/areas/admin/**\/web/`, held to
 *  the web so a change there is noticed here. */

describe("settings words", () => {
    it("counts and lists as a person says it", () => {
        expect(count(1, "person", "people")).toBe("1 person");
        expect(count(3, "person", "people")).toBe("3 people");
        expect(firstFew(["Researcher", "Writer", "Editor", "Critic"])).toBe("Researcher, Writer +2");
        expect(firstFew(["Slack", "Email"], 3)).toBe("Slack, Email");
    });

    it("says the server's own words, never a transport's", () => {
        expect(problemOf(new Error("Name is taken."), "fallback")).toBe("Name is taken.");
        expect(problemOf(new Error("Network request failed"), "Could not reach Lemma.")).toBe("Could not reach Lemma.");
        expect(problemOf("nope", "fallback")).toBe("fallback");
        expect(problemOf(new Error("Not Found"), "The seats could not be read.")).toBe("The seats could not be read.");
        expect(problemOf(new Error("Forbidden."), "fallback")).toBe("fallback");
        expect(problemOf(new Error("Not found: that invitation was withdrawn."), "fallback")).toBe("Not found: that invitation was withdrawn.");
    });

    it("tells a refusal and a missing thing from a failure", () => {
        const refused = Object.assign(new Error("nope"), { statusCode: 403 });
        expect(statusOf(refused)).toBe(403);
        expect(isForbidden(refused)).toBe(true);
        expect(isForbidden(new Error("boom"))).toBe(false);
        expect(isMissing(Object.assign(new Error("gone"), { status: 404 }))).toBe(true);
        expect(statusOf(new Error("offline"))).toBeNull();
    });
});

describe("where settings live", () => {
    it("lists every section once, and hides billing on a local installation", () => {
        expect(new Set(SETTINGS.map((one) => one.id)).size).toBe(SETTINGS.length);
        expect(offeredSettings("local").map((one) => one.id)).not.toContain("plan");
        expect(offeredSettings("local").map((one) => one.id)).not.toContain("team-billing");
        expect(offeredSettings("hosted")).toHaveLength(SETTINGS.length);
        expect(offeredSettings(null)).toHaveLength(SETTINGS.length);
        expect(settingsEntry("models")?.title).toBe("Models");
        expect(settingsEntry("nope")).toBeNull();
    });

    it("names a Lemma's parts in the phone's words", () => {
        expect(lemmaPart("works-with")?.title("Support")).toBe("Lemmas Support works with");
        expect(lemmaPart("channels")?.title("Support")).toBe("Where to reach Support");
        for (const part of LEMMA_PARTS) {
            const said = [part.label, part.title("Support"), part.note("Support"), part.blurb].join(" ");
            expect(said).not.toMatch(/\b(teammate|pod|bot|channel|goal|mission)s?\b/i);
        }
    });
});

describe("a Lemma's face and name", () => {
    /* A stand-in hash, so the search is checked without the cast's pictures. */
    const cast = ["arch", "bean", "bloom"];
    const characterOf = (seed: string) => cast[[...seed].reduce((sum, char) => sum + char.charCodeAt(0), 0) % cast.length];

    it("finds the variant of a Lemma's id that draws the character asked for", () => {
        for (const wanted of cast) {
            const variant = variantFor("pod-1", wanted, characterOf);
            expect(variant).not.toBeNull();
            expect(characterOf(variantSeed("pod-1", variant!))).toBe(wanted);
        }
        expect(variantSeed("pod-1", 0)).toBe("pod-1");
        expect(identityIcon(7)).toBe("lemma-identity:7");
        expect(variantFor("pod-1", "nobody", characterOf)).toBeNull();
    });

    it("says when it was started and by whom", () => {
        const now = new Date("2026-10-24T12:00:00Z");
        expect(startedLine("2026-10-12T09:00:00Z", "Sam Okafor", now)).toBe("Started 12 October by Sam Okafor");
        expect(startedLine("2025-03-03T09:00:00Z", null, now)).toBe("Started 3 March 2025");
        expect(startedLine(null, "Sam", now)).toBe("");
        expect(startedLine("not a date", null, now)).toBe("");
    });

    it("asks for the name the way the web does before deleting", () => {
        expect(namesTeammate("support ", "Support")).toBe(true);
        expect(namesTeammate("Sup", "Support")).toBe(false);
        expect(namesTeammate("", "")).toBe(false);
    });
});

describe("a provider sign-in coming back", () => {
    it("keeps the app's own return out of the router", () => {
        expect(isConnectReturn("lemma://connect/done?connect=connected")).toBe(true);
        expect(isConnectReturn("/connect/done")).toBe(true);
        expect(isConnectReturn("connect/done?x=1")).toBe(true);
        expect(isConnectReturn("/lemma/abc")).toBe(false);
    });

    it("reads and says the outcome", () => {
        const outcome = outcomeOf("lemma://connect/done?connect=error&connector=slack&reason=Denied");
        expect(outcome).toEqual({ connect: "error", connector: "slack", account: null, reason: "Denied", code: null });
        expect(outcomeNote(outcome!)).toEqual({ text: "Denied", bad: true });
        expect(outcomeNote({ connect: "connected", connector: null, account: null, reason: null }).bad).toBe(false);
        expect(outcomeOf("lemma://connect/done")).toBeNull();
    });
});
