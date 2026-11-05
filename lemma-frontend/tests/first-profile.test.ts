import test from "node:test";
import assert from "node:assert/strict";
import type { UserResponse } from "lemma-sdk";
import {
    asksFirstProfile, canDismissFirstProfile, firstProfileNeeds, needsTimezoneSync, NEW_ACCOUNT_MS, settledKey, syncDetectedTimezone,
} from "../src/session/first-profile.ts";

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

test("a new account with no timezone is synced only while the dialog is closed", () => {
    const fresh = user();
    assert.equal(needsTimezoneSync(fresh, { dialogOpen: true, now: NOW }), false);
    assert.equal(needsTimezoneSync(fresh, { dialogOpen: false, now: NOW }), true);
});

test("profile dismissal stays closed to every path while saving", () => {
    assert.equal(canDismissFirstProfile(true), false);
    assert.equal(canDismissFirstProfile(false), true);
});

test("an account that already has a timezone is not overwritten", () => {
    assert.equal(
        needsTimezoneSync(user({ timezone: "Europe/Berlin" }), { dialogOpen: false, now: NOW }),
        false,
    );
    assert.equal(
        needsTimezoneSync(user({ timezone: "" }), { dialogOpen: false, now: NOW }),
        false,
    );
    assert.equal(
        needsTimezoneSync(user({ timezone: "   " }), { dialogOpen: false, now: NOW }),
        false,
    );
});

test("only new accounts get a background timezone: older ones keep an empty zone", () => {
    const old = user({ created_at: new Date(NOW - NEW_ACCOUNT_MS - 1).toISOString() });
    const recent = user({ created_at: new Date(NOW - NEW_ACCOUNT_MS + 60_000).toISOString() });
    assert.equal(needsTimezoneSync(old, { dialogOpen: false, now: NOW }), false);
    assert.equal(needsTimezoneSync(recent, { dialogOpen: false, now: NOW }), true);
    assert.equal(needsTimezoneSync(user({ created_at: "not a date" }), { dialogOpen: false, now: NOW }), false);
});

test("a failed timezone save retries after 1s, 2s and 4s, then stops", async context => {
    context.mock.timers.enable({ apis: ["setTimeout"] });
    const attempts: Array<{ reject: () => void }> = [];
    let calls = 0;
    const stop = syncDetectedTimezone(
        () => new Promise<void>((_resolve, reject) => {
            calls += 1;
            attempts.push({ reject });
        }),
        () => assert.fail("a failed save must not succeed"),
        () => {},
    );

    const fail = async (delay: number) => {
        attempts.at(-1)!.reject();
        await Promise.resolve();
        context.mock.timers.tick(delay);
        await Promise.resolve();
    };
    assert.equal(calls, 1);
    await fail(1_000);
    assert.equal(calls, 2);
    await fail(2_000);
    assert.equal(calls, 3);
    await fail(4_000);
    assert.equal(calls, 4);
    context.mock.timers.tick(4_000);
    assert.equal(calls, 4);
    stop();
});

test("a successful timezone save cancels the remaining retries", async context => {
    context.mock.timers.enable({ apis: ["setTimeout"] });
    const attempts: Array<{ resolve: () => void; reject: () => void }> = [];
    let calls = 0;
    let successes = 0;
    const stop = syncDetectedTimezone(
        () => new Promise<void>((resolve, reject) => {
            calls += 1;
            attempts.push({ resolve, reject });
        }),
        () => { successes += 1; },
        () => {},
    );

    attempts[0].reject();
    await Promise.resolve();
    context.mock.timers.tick(1_000);
    await Promise.resolve();
    assert.equal(calls, 2);
    attempts[1].resolve();
    await Promise.resolve();
    context.mock.timers.tick(4_000);
    assert.equal(calls, 2);
    assert.equal(successes, 1);
    stop();
});

test("cleanup ignores a stale timezone save and prevents its retries", async context => {
    context.mock.timers.enable({ apis: ["setTimeout"] });
    let calls = 0;
    let cacheUpdates = 0;
    let resolveOld!: () => void;
    const stopAfterSuccess = syncDetectedTimezone(
        () => new Promise<void>((resolve) => {
            calls += 1;
            resolveOld = resolve;
        }),
        () => { cacheUpdates += 1; },
        () => {},
    );

    stopAfterSuccess();
    resolveOld();
    await Promise.resolve();
    context.mock.timers.tick(10_000);
    assert.equal(calls, 1);
    assert.equal(cacheUpdates, 0);

    let rejectOld!: () => void;
    const stopAfterFailure = syncDetectedTimezone(
        () => new Promise<void>((_resolve, reject) => {
            calls += 1;
            rejectOld = reject;
        }),
        () => { cacheUpdates += 1; },
        () => {},
    );
    stopAfterFailure();
    rejectOld();
    await Promise.resolve();
    context.mock.timers.tick(10_000);
    assert.equal(calls, 2);
    assert.equal(cacheUpdates, 0);
});
