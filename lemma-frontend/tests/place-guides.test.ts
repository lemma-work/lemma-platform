import test from "node:test";
import assert from "node:assert/strict";
import { guideFor, guideTitle, placeOf, type GuidePlace } from "../src/tour/guides.ts";

const PLACES: GuidePlace[] = ["pages", "workflows", "tables", "apps"];
const guides = PLACES.map((place) => guideFor(place, "Hazel"));

test("each place has a guide of a few lines, titled for the place", () => {
    for (const guide of guides) {
        assert.equal(guide.title, guideTitle(guide.place));
        assert.match(guide.title, /^How \w+ work$/);
        assert.ok(guide.lines.length >= 3 && guide.lines.length <= 5, guide.place + " has " + guide.lines.length + " lines");
        assert.equal(new Set(guide.lines.map((line) => line.gesture)).size, guide.lines.length, guide.place + " repeats a gesture");
    }
});

test("a line is a short gesture and one sentence of what it does", () => {
    for (const guide of guides) {
        for (const line of guide.lines) {
            assert.ok(line.gesture.length > 0 && line.gesture.length <= 12, guide.place + ": gesture too long: " + line.gesture);
            assert.match(line.what, /\.$/, guide.place + ": " + line.what);
            assert.doesNotMatch(line.what, /[.!?]\s+\S/, guide.place + " says more than one sentence: " + line.what);
        }
    }
});

test("the teammate is called by its name, never by a category word", () => {
    for (const guide of guides) {
        const said = [guide.title, ...guide.lines.flatMap((line) => [line.gesture, line.what]), guide.tryIt?.label ?? "", guide.tryIt?.kind === "ask" ? guide.tryIt.text : ""];
        for (const text of said) {
            assert.doesNotMatch(text, /\b(teammates?|bots?|assistants?|agents?|pods?)\b/i, guide.place + ": " + text);
        }
        assert.ok(said.some((text) => text.includes("Hazel")), guide.place + " never names who does the work");
    }
});

test("trying a place needs no typing", () => {
    // An ask is put in the chat box, never sent from the card, so it has to
    // be a sentence that can go as it stands.
    for (const guide of guides) {
        if (guide.tryIt?.kind === "ask") assert.match(guide.tryIt.text, /[.?!]$/, guide.place);
    }
    assert.equal(guideFor("pages", "Hazel").tryIt?.kind, "page-guide");
});

test("pages teach the gestures nothing on screen advertises", () => {
    const gestures = guideFor("pages", "Hazel").lines.map((line) => line.gesture);
    assert.deepEqual(gestures, ["/", "Select", "@Hazel", "Ask"]);
});

test("what is on screen decides the guide", () => {
    assert.equal(placeOf({ id: "space:pages", kind: "space" }), "pages");
    assert.equal(placeOf({ id: "file:/pages/Plan.md", kind: "file", path: "/pages/Plan.md" }), "pages");
    assert.equal(placeOf({ id: "file:/me/notes.md", kind: "file", path: "/me/notes.md" }), "pages");
    assert.equal(placeOf({ id: "file:/report.pdf", kind: "file", path: "/report.pdf" }), null);
    assert.equal(placeOf({ id: "space:workflows", kind: "space" }), "workflows");
    assert.equal(placeOf({ id: "workflow:intake", kind: "workflow" }), "workflows");
    assert.equal(placeOf({ id: "run:r1", kind: "run" }), "workflows");
    assert.equal(placeOf({ id: "space:tables", kind: "space" }), "tables");
    assert.equal(placeOf({ id: "table:tasks", kind: "table" }), "tables");
    assert.equal(placeOf({ id: "record:tasks:1", kind: "record" }), "tables");
    assert.equal(placeOf({ id: "space:apps", kind: "space" }), "apps");
    assert.equal(placeOf({ id: "app:board", kind: "app" }), "apps");
    // About explains itself, section by section; Home and chats are not places with gestures to learn.
    assert.equal(placeOf({ id: "space:about", kind: "space" }), null);
    assert.equal(placeOf({ id: "space:home", kind: "space" }), null);
    assert.equal(placeOf({ id: "conversation", kind: "conversation" }), null);
    assert.equal(placeOf(undefined), null);
});
