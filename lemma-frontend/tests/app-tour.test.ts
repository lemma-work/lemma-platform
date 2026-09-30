import test from "node:test";
import assert from "node:assert/strict";
import { tourStops } from "../src/tour/stops.ts";
import { offersTour, TOUR_WINDOW_MS, tourSeenKey } from "../src/tour/when.ts";

const NOW = Date.parse("2026-10-01T12:00:00Z");
const stops = tourStops({ name: "Hazel", org: "Acme" });

test("the tour says what Lemma is first, then points at real controls", () => {
    assert.equal(stops[0].id, "welcome");
    assert.equal(stops[0].target, null);
    assert.equal(stops[0].side, "center");
    for (const stop of stops.slice(1)) assert.ok(stop.target, stop.id + " points at nothing");
    assert.equal(new Set(stops.map((stop) => stop.id)).size, stops.length);
    assert.equal(new Set(stops.map((stop) => stop.target)).size, stops.length);
});

test("it stays short: a welcome and six stops", () => {
    assert.equal(stops.length, 7);
});

test("every stop is one sentence", () => {
    for (const stop of stops) {
        assert.match(stop.line, /\.$/, stop.id);
        assert.doesNotMatch(stop.line, /[.!?]\s+\S/, stop.id + " says more than one sentence: " + stop.line);
    }
});

test("it speaks of the teammate by name, and names the category only where it is the subject", () => {
    for (const stop of stops) {
        const said = stop.title + " " + stop.line;
        assert.doesNotMatch(said, /\b(bots?|assistants?|agents?|pods?)\b/i, stop.id + ": " + said);
        if (stop.id !== "welcome" && stop.id !== "rail") {
            assert.doesNotMatch(said, /\bteammates?\b/i, stop.id + " should use the name: " + said);
        }
    }
    assert.match(stops[0].title, /^Hazel is an AI teammate\. This is its space\.$/);
    assert.ok(stops.filter((stop) => (stop.title + stop.line).includes("Hazel")).length >= 5);
    assert.match(stops.find((stop) => stop.id === "rail")!.line, /Acme/);
});

test("stops that live in the sidebar say so, so a phone can open its drawer first", () => {
    const area = Object.fromEntries(stops.map((stop) => [stop.id, stop.area]));
    assert.deepEqual(area, { welcome: "none", rail: "sidebar", places: "sidebar", ask: "stage", needs: "stage", share: "stage", about: "sidebar" });
});

test("the tour offers itself only in an account's first week, and only once", () => {
    const fresh = new Date(NOW - 60 * 60 * 1000).toISOString();
    assert.equal(offersTour(fresh, { seen: false, now: NOW }), true);
    assert.equal(offersTour(fresh, { seen: true, now: NOW }), false);
    const old = new Date(NOW - TOUR_WINDOW_MS - 1).toISOString();
    assert.equal(offersTour(old, { seen: false, now: NOW }), false);
});

test("an account with no readable creation date is never interrupted", () => {
    assert.equal(offersTour(undefined, { seen: false, now: NOW }), false);
    assert.equal(offersTour("", { seen: false, now: NOW }), false);
    assert.equal(offersTour("not a date", { seen: false, now: NOW }), false);
});

test("having seen it is remembered per person", () => {
    assert.notEqual(tourSeenKey("u1"), tourSeenKey("u2"));
    assert.match(tourSeenKey("u1"), /tour-seen:u1$/);
});
