import test from "node:test";
import assert from "node:assert/strict";
import type { UserResponse } from "lemma-sdk";
import { asksFirstProfile, firstProfileNeeds, NEW_ACCOUNT_MS, settledKey } from "../src/session/first-profile.ts";

const NOW = Date.parse("2026-09-25T12:00:00Z");

function user(over: Partial<UserResponse> = {}): UserResponse {
    return {
        id: "u1", email: "sam@example.com", created_at: "2026-09-25T11:00:00Z", updated_at: "2026-09-25T11:00:00Z",
        is_active: true, is_superuser: false, is_verified: true, ...over,
    } as UserResponse;
}

test("a new account with no name is asked, whether or not WhatsApp can verify here", () => {
    assert.equal(asksFirstProfile(user(), { whatsApp: false, settled: false, now: NOW }), true);
    assert.equal(asksFirstProfile(user(), { whatsApp: true, settled: false, now: NOW }), true);
});

test("a named account is asked only for the phone, and only where a phone can be proved", () => {
    // Without WhatsApp verification the step would have to take a typed number
    // on trust, so a named account has nothing left to be asked.
    const named = user({ first_name: "Sam" });
    assert.equal(asksFirstProfile(named, { whatsApp: false, settled: false, now: NOW }), false);
    assert.equal(asksFirstProfile(named, { whatsApp: true, settled: false, now: NOW }), true);
    assert.deepEqual(firstProfileNeeds(named, { whatsApp: true }), { name: false, phone: true });
});

test("an account that already has both is never shown the step", () => {
    const complete = user({ first_name: "Sam", mobile_number: "+14155552671" });
    assert.equal(asksFirstProfile(complete, { whatsApp: true, settled: false, now: NOW }), false);
});

test("a blank name is no name", () => {
    assert.equal(firstProfileNeeds(user({ first_name: "   " }), { whatsApp: false }).name, true);
});

test("skipping or continuing once is the end of it", () => {
    assert.equal(asksFirstProfile(user(), { whatsApp: true, settled: true, now: NOW }), false);
});

test("only new accounts: an older one missing both is left alone", () => {
    const old = user({ created_at: new Date(NOW - NEW_ACCOUNT_MS - 1).toISOString() });
    const recent = user({ created_at: new Date(NOW - NEW_ACCOUNT_MS + 60_000).toISOString() });
    assert.equal(asksFirstProfile(old, { whatsApp: true, settled: false, now: NOW }), false);
    assert.equal(asksFirstProfile(recent, { whatsApp: true, settled: false, now: NOW }), true);
});

test("an unreadable sign-up date is treated as old, not new", () => {
    assert.equal(asksFirstProfile(user({ created_at: "not a date" }), { whatsApp: true, settled: false, now: NOW }), false);
});

test("what was skipped is remembered per person", () => {
    assert.notEqual(settledKey("u1"), settledKey("u2"));
});
