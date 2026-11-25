import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { GUIDE_PLACES, TOUR_WINDOW_MS, cardPlace, guideFor, isGuidePlace, lit, offersTour, scrimPath, tourStops } from "../src/areas/more/tour/model";

/** The tour and the guides (PARITY.md, G): the phone's stops, the web's rule
 *  for when it offers itself, where the card sits, and the copy rules. */

const web = fileURLToPath(new URL("../../lemma-frontend/src/", import.meta.url));

describe("when the tour offers itself", () => {
    const now = Date.parse("2026-10-24T12:00:00Z");
    it("offers once, in the account's first week", () => {
        expect(offersTour("2026-10-22T09:00:00Z", { seen: false, now })).toBe(true);
        expect(offersTour("2026-10-22T09:00:00Z", { seen: true, now })).toBe(false);
        expect(offersTour(new Date(now - TOUR_WINDOW_MS - 1).toISOString(), { seen: false, now })).toBe(false);
    });

    it("does not guess when the account's age is unknown", () => {
        expect(offersTour(null, { seen: false, now })).toBe(false);
        expect(offersTour("not a date", { seen: false, now })).toBe(false);
    });

    it.runIf(existsSync(web + "tour/when.ts"))("keeps the web's week", () => {
        expect(readFileSync(web + "tour/when.ts", "utf8")).toContain("export const TOUR_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;");
    });
});

describe("the stops", () => {
    const stops = tourStops("Acme");

    it("starts with a welcome that points at nothing, then lights a real control each", () => {
        expect(stops[0]).toMatchObject({ id: "welcome", target: null });
        expect(stops.slice(1).every((stop) => stop.target === stop.id)).toBe(true);
        expect(new Set(stops.map((stop) => stop.id)).size).toBe(stops.length);
    });

    it("names the organization where it knows it, and reads without it", () => {
        expect(stops.find((stop) => stop.id === "lemmas")?.line).toContain("at Acme");
        expect(tourStops(null).find((stop) => stop.id === "lemmas")?.line).not.toContain(" at ");
    });

    it("says one sentence per stop, in the phone's words", () => {
        for (const stop of stops) {
            expect(stop.line.split(/(?<=\.)\s+/).length).toBeLessThanOrEqual(2);
            expect(`${stop.title} ${stop.line}`).not.toMatch(/\b(pod|teammate|bot|assistant|workspace|channel)s?\b/i);
            expect(`${stop.title} ${stop.line}`).not.toMatch(/—|!/);
        }
    });
});

describe("where the card sits", () => {
    const view = { width: 400, height: 860 };
    const insets = { top: 40, bottom: 24 };

    it("is centred when there is nothing to point at", () => {
        expect(cardPlace(null, view, insets).edge).toBe("center");
    });

    it("goes along the edge the control is not near", () => {
        expect(cardPlace({ x: 0, y: 780, width: 130, height: 60 }, view, insets)).toMatchObject({ edge: "top", top: 52 });
        expect(cardPlace({ x: 300, y: 50, width: 44, height: 44 }, view, insets)).toMatchObject({ edge: "bottom", bottom: 36 });
    });

    it("pads the light and keeps it on screen", () => {
        expect(lit({ x: 10, y: 10, width: 44, height: 44 }, view)).toEqual({ x: 4, y: 4, width: 56, height: 56 });
        expect(lit({ x: 0, y: 820, width: 400, height: 40 }, view)).toEqual({ x: 2, y: 814, width: 396, height: 44 });
    });

    it("dims everything but the light", () => {
        expect(scrimPath(view, null)).toBe("M0,0H400V860H0Z");
        const holed = scrimPath(view, { x: 4, y: 4, width: 56, height: 56 });
        expect(holed.startsWith("M0,0H400V860H0Z")).toBe(true);
        expect(holed).toContain("A12,12");
    });
});

describe("how each place works", () => {
    it("has a guide for every place, gestures a thumb makes", () => {
        for (const place of GUIDE_PLACES) {
            const guide = guideFor(place, "Support");
            expect(guide.lines.length).toBeGreaterThan(2);
            for (const line of guide.lines) {
                expect(line.what).toMatch(/\.$/);
                expect(line.what).not.toMatch(/bottom right|click|hover|\bkey\b|⌘/i);
                expect(`${line.gesture} ${line.what}`).not.toMatch(/\b(pod|teammate|bot|assistant|workspace)s?\b/i);
            }
        }
    });

    it("names the Lemma it is about, or says the Lemma when none is chosen", () => {
        expect(guideFor("tables", "Books").lines.some((line) => line.what.includes("Books"))).toBe(true);
        expect(guideFor("tables", null).lines.some((line) => line.what.includes("the Lemma"))).toBe(true);
    });

    it("tries a place without sending anything for the person", () => {
        expect(guideFor("pages", "Support").tryIt).toEqual({ kind: "page-guide", label: "Open the guide page" });
        expect(guideFor("workflows", "Support").tryIt?.kind).toBe("ask");
    });

    it("reads a place from an address and refuses anything else", () => {
        expect(isGuidePlace("tables")).toBe(true);
        expect(isGuidePlace("settings")).toBe(false);
        expect(isGuidePlace(undefined)).toBe(false);
    });
});
