import test from "node:test";
import assert from "node:assert/strict";
import type { UserResponse } from "lemma-sdk";
import {
    changes, displayName, draftOf, hasChanges, isTimezone, localTimezone, problems, timezones,
} from "../src/session/profile-edit.ts";

function user(over: Partial<UserResponse> = {}): UserResponse {
    return {
        id: "u1", email: "sam@example.com", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
        is_active: true, is_superuser: false, is_verified: true, ...over,
    } as UserResponse;
}

test("a profile with nothing filled in is a form with nothing in it, not undefined", () => {
    const draft = draftOf(user());

    assert.deepEqual(draft, {
        first_name: "", last_name: "", timezone: "", country: "",
        mobile_number: "", telegram_username: "", date_of_birth: "",
    });
    assert.deepEqual(draftOf(null), draftOf(undefined));
});

test("a telegram handle is held without its @, however it was typed", () => {
    // It is shown and typed with one and stored without; normalising on the way
    // in is what stops "@sam" and "sam" reading as a change every time.
    assert.equal(draftOf(user({ telegram_username: "@sam" })).telegram_username, "sam");
    const before = draftOf(user({ telegram_username: "sam" }));
    assert.equal(hasChanges(before, { ...before, telegram_username: "@sam" }), false);
    assert.equal(hasChanges(before, { ...before, telegram_username: "@@sam" }), false);
});

test("only what changed is sent", () => {
    // A form that posts every field can write a stale copy over a value that
    // moved while it was open.
    const before = draftOf(user({ first_name: "Sam", timezone: "Asia/Kolkata" }));
    const after = { ...before, timezone: "Europe/Berlin" };

    assert.deepEqual(changes(before, after), { timezone: "Europe/Berlin" });
    assert.deepEqual(changes(before, before), {});
    assert.equal(hasChanges(before, before), false);
});

test("a field emptied on purpose is sent as null, not left out", () => {
    // Omitting it would leave the old value set forever, so clearing a phone
    // number would appear to work and silently not.
    const before = draftOf(user({ mobile_number: "+44 7700 900000", country: "GB" }));
    const after = { ...before, mobile_number: "" };

    assert.deepEqual(changes(before, after), { mobile_number: null });
});

test("whitespace is not an edit", () => {
    const before = draftOf(user({ first_name: "Sam" }));

    assert.deepEqual(changes(before, { ...before, first_name: "  Sam  " }), {});
    assert.deepEqual(changes(before, { ...before, first_name: "   " }), { first_name: null });
});

test("a birthday must be a real date that has already happened", () => {
    const today = new Date("2026-09-18T00:00:00Z");
    const base = draftOf(user());

    assert.equal(problems({ ...base, date_of_birth: "1990-04-23" }, today).date_of_birth, undefined);
    assert.ok(problems({ ...base, date_of_birth: "23/04/1990" }, today).date_of_birth);
    assert.ok(problems({ ...base, date_of_birth: "1990-13-45" }, today).date_of_birth);
    assert.ok(problems({ ...base, date_of_birth: "2099-01-01" }, today).date_of_birth);
    // empty is not a problem — the field is optional
    assert.equal(problems(base, today).date_of_birth, undefined);
});

test("a timezone is checked against the runtime that will format the dates", () => {
    // Not a hardcoded list: the list changes, and what matters is whether the
    // browser doing the formatting recognises it.
    assert.equal(isTimezone("Asia/Kolkata"), true);
    assert.equal(isTimezone("UTC"), true);
    assert.equal(isTimezone("Mars/Olympus"), false);
    assert.equal(isTimezone(""), false);

    const base = draftOf(user());
    assert.ok(problems({ ...base, timezone: "Nowhere/Nothing" }).timezone);
    assert.equal(problems({ ...base, timezone: "Europe/London" }).timezone, undefined);
    assert.equal(problems(base).timezone, undefined);
});

test("the zone list contains the zone this machine is in", () => {
    const here = localTimezone();
    assert.ok(here.length > 0, "a browser that cannot name its own zone would offer a list missing it");
    assert.ok(timezones().includes(here));
    assert.ok(timezones().length > 5);
});

test("the zone list contains the zone any machine is in, including UTC", () => {
    /* The one above only ever tested whoever ran it. On a laptop it passes and
       says nothing; on a CI runner it failed for a year's worth of commits,
       because `supportedValuesOf` lists 418 canonical zones and `UTC` — what
       every runner and most servers report — is not among them. Neither is
       `Etc/UTC`, nor `US/Pacific`, and which of `Asia/Calcutta` and
       `Asia/Kolkata` is canonical depends on the ICU build underneath.

       So this one moves the machine instead of asking where it is. A picker
       that cannot show somebody the zone they are in cannot show them their
       own setting, whatever their zone happens to be called. */
    const was = process.env.TZ;
    try {
        for (const zone of ["UTC", "Etc/UTC", "US/Pacific", "America/New_York", "Asia/Kolkata"]) {
            process.env.TZ = zone;
            const here = localTimezone();
            assert.ok(here.length > 0, "TZ=" + zone + " left the runtime unable to name its own zone");
            assert.ok(timezones().includes(here), "TZ=" + zone + " resolves to " + here + ", which the list is missing");
        }
    } finally {
        /* Restored whatever happened, or every dated assertion after this one
           reads its clock in the wrong zone. */
        if (was === undefined) delete process.env.TZ;
        else process.env.TZ = was;
    }
});

test("somebody with no name is still called something", () => {
    assert.equal(displayName(user({ first_name: "Sam", last_name: "Roy" })), "Sam Roy");
    assert.equal(displayName(user({ first_name: "Sam" })), "Sam");
    assert.equal(displayName(user()), "sam");           // falls back to the email's local part
    assert.equal(displayName(null), "Your profile");
});
