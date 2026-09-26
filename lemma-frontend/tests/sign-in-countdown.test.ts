import test from "node:test";
import assert from "node:assert/strict";
import { timeLeft } from "../src/desktop/sign-in-countdown.ts";

test("the time left is a clock, rounded up to the second", () => {
    assert.equal(timeLeft(10 * 60_000, 0), "10:00");
    assert.equal(timeLeft(61_000, 0), "1:01");
    assert.equal(timeLeft(1_500, 1_000), "0:01");
});

test("an expired request reads zero, never negative", () => {
    assert.equal(timeLeft(1_000, 5_000), "0:00");
});
