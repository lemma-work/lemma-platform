import { test } from "node:test";
import assert from "node:assert/strict";
import { listStamp } from "../src/data/stamp.ts";

/* Sunday 27 September 2026, mid-afternoon, local time. */
const now = new Date(2026, 8, 27, 15, 0);
const at = (...parts: [number, number, number, number?, number?]) => new Date(...parts).toISOString();

test("today is the time, not the word", () => {
    assert.match(listStamp(at(2026, 8, 27, 13, 33), now), /13.33|1.33/);
});

test("the last six days are a weekday", () => {
    const yesterday = listStamp(at(2026, 8, 26, 23, 0), now);
    const sixBack = listStamp(at(2026, 8, 21, 0, 30), now);
    assert.equal(yesterday, new Date(2026, 8, 26).toLocaleDateString([], { weekday: "short" }));
    assert.equal(sixBack, new Date(2026, 8, 21).toLocaleDateString([], { weekday: "short" }));
});

test("a week back is a date, because that weekday is today's", () => {
    assert.equal(listStamp(at(2026, 8, 20, 12), now), new Date(2026, 8, 20).toLocaleDateString([], { day: "numeric", month: "short" }));
});

test("another year says so", () => {
    assert.match(listStamp(at(2025, 11, 2, 12), now), /2025/);
});

test("nothing, or nonsense, is empty", () => {
    assert.equal(listStamp(null, now), "");
    assert.equal(listStamp("not a date", now), "");
});
