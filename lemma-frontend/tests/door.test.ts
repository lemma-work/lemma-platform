import test from "node:test";
import assert from "node:assert/strict";
import { doorFor } from "../src/session/auth-state.ts";
import { RECENT_MS, tripIsRecent } from "../src/session/portal-trip.ts";

/** What somebody signed out meets at the workspace. */

test("a signed-out visitor is taken to the door, not shown a picture of it", () => {
    assert.equal(doorFor(false, false), "send");
});

test("coming back still signed out stops instead of bouncing again", () => {
    // Otherwise it is an infinite round trip between the app and the portal.
    assert.equal(doorFor(false, true), "stalled");
});

test("a token the API refuses outranks the trip, which could not fix it", () => {
    assert.equal(doorFor(true, false), "token");
    assert.equal(doorFor(true, true), "token");
});

test("a trip to sign in counts as recent for two minutes, then expires on its own", () => {
    const at = 1_000_000;
    assert.equal(tripIsRecent(String(at), at), true);
    assert.equal(tripIsRecent(String(at), at + RECENT_MS - 1), true);
    /* Expiring, not being cleared when the workspace says "in", is what stops
       the workspace and the portal handing a tab back and forth. */
    assert.equal(tripIsRecent(String(at), at + RECENT_MS), false);
});

test("no trip, or one written by an older build, is not a recent trip", () => {
    assert.equal(tripIsRecent(null, 1_000_000), false);
    assert.equal(tripIsRecent("1", 1_000_000), false);
    assert.equal(tripIsRecent("not a time", 1_000_000), false);
    assert.equal(tripIsRecent(String(2_000_000), 1_000_000), false);
});
