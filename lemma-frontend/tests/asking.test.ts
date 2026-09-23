import test from "node:test";
import assert from "node:assert/strict";
import { ASKS, typedAt } from "../src/stage/asking.ts";

const ONE = ["abcd"];

test("it starts empty and types the phrase out a character at a time", () => {
    assert.equal(typedAt(ONE, 0), "");
    assert.equal(typedAt(ONE, 1), "a");
    assert.equal(typedAt(ONE, 46), "a");
    assert.equal(typedAt(ONE, 47), "ab");
    assert.equal(typedAt(ONE, 4 * 46 - 1), "abcd");
});

test("it holds the whole phrase, then takes it back off the end", () => {
    const typed = 4 * 46;
    assert.equal(typedAt(ONE, typed + 500), "abcd");
    assert.equal(typedAt(ONE, typed + 1699), "abcd");
    assert.equal(typedAt(ONE, typed + 1700), "abcd");
    assert.equal(typedAt(ONE, typed + 1700 + 1), "abc");
    assert.equal(typedAt(ONE, typed + 1700 + 23), "ab");
});

test("the gap between phrases is empty, so the caller can put its own text back", () => {
    // A field that flashes blank between two suggestions looks broken; the
    // shelf puts "I need someone to…" back in this window.
    const span = 4 * 46 + 1700 + 4 * 22 + 280;
    assert.equal(typedAt(ONE, span - 1), "");
    assert.equal(typedAt(ONE, span - 280), "");
});

test("it comes round to the first phrase again", () => {
    const span = 4 * 46 + 1700 + 4 * 22 + 280;
    assert.equal(typedAt(ONE, span + 1), "a");
});

test("each phrase gets its turn, in order", () => {
    const two = ["ab", "xy"];
    const first = 2 * 46 + 1700 + 2 * 22 + 280;
    assert.equal(typedAt(two, 1), "a");
    assert.equal(typedAt(two, first + 1), "x");
    assert.equal(typedAt(two, first + 46), "x");
    assert.equal(typedAt(two, first + 47), "xy");
    assert.equal(typedAt(two, first + 2 * 46 + 500), "xy");
});

test("a clock that has not started, or went backwards, shows nothing rather than throwing", () => {
    // A suspended tab can hand this a negative elapsed time on resume.
    assert.equal(typedAt(ONE, -5000), "");
    assert.equal(typedAt([], 1234), "");
});

test("nothing on the shelf is offered back as a suggestion", async () => {
    // The sentence above the grid says the grid is not the menu. Repeating a
    // card here says the opposite.
    const { HIRES } = await import("../src/data/hires.ts");
    const listed = new Set(HIRES.map((hire) => hire.name.toLowerCase()));
    for (const ask of ASKS) assert.equal(listed.has(ask.toLowerCase()), false, ask);
    assert.ok(ASKS.length >= 3);
});
