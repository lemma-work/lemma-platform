import test from "node:test";
import assert from "node:assert/strict";
import { doorFor } from "../src/session/auth-state.ts";

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
