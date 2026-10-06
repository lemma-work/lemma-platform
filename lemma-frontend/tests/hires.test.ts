import test from "node:test";
import assert from "node:assert/strict";
import { BLANK, EXPLORING_OPENERS, HIRES, blankHire, dealtName, openersFor } from "../src/data/hires.ts";
import { characterForSeed } from "../src/shell/cast.ts";

test("every listing offers something to say on the first morning", () => {
    // The reveal's whole middle section comes from this. A listing that added
    // itself to the shelf without openers would draw a card with a heading
    // and nothing under it.
    for (const hire of HIRES) {
        assert.ok(hire.openers.length > 0, hire.id + " has no openers");
        assert.ok(hire.openers.length <= 3, hire.id + " has more openers than the card shows");
    }
});

test("a blank hire is offered the job it was described as, not an invented one", () => {
    assert.deepEqual(
        openersFor(BLANK, "  watch the inbox and answer what you can  "),
        ["Watch the inbox and answer what you can."],
    );
});

test("a described job that already ends in punctuation is not given a second full stop", () => {
    assert.deepEqual(openersFor(BLANK, "Who owes us money?"), ["Who owes us money?"]);
});

test("a blank hire nobody described is offered ways to explore, not a job", () => {
    // Somebody who hired "just exploring" lands on the reveal with nothing
    // typed. An empty section left them on a blank thread; an invented job
    // would put words in their mouth. Questions to the teammate do neither.
    assert.deepEqual(openersFor(BLANK, "   "), EXPLORING_OPENERS);
});

test("somebody hired without a name is named after the face they were dealt", () => {
    // One click hires them, so the name comes with the face rather than from
    // a list of its own that could disagree with it.
    const somebody = blankHire("explore-1");
    const name = dealtName(somebody);
    assert.equal(name.toLowerCase(), characterForSeed(somebody.seed));
    assert.match(name, /^[A-Z][a-z]+$/);
});

test("a listing's openers ignore whatever was typed on the shelf", () => {
    // The job box is the road to the blank hire. Somebody who typed into it
    // and then took Follow-ups off the shelf is getting Follow-ups.
    const followUps = HIRES.find((hire) => hire.id === "follow-ups");
    assert.ok(followUps);
    assert.deepEqual(openersFor(followUps, "something else entirely"), followUps.openers);
});

test("each blank hire is dealt a face of its own", () => {
    // The shared BLANK seed hashed to one character, and hiring pins the
    // picked character onto the pod — so every described job came out as Kite.
    const faces = new Set(
        Array.from({ length: 48 }, (_, index) => characterForSeed(blankHire("nonce-" + index).seed)),
    );
    assert.ok(faces.size > 8, "48 blank hires drew only " + faces.size + " characters");
    assert.equal(blankHire("a").role, BLANK.role);
});
