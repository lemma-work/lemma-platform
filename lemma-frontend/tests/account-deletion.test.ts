import test from "node:test";
import assert from "node:assert/strict";
import { confirmsAccount, deletionRefusal, DELETION_CONSEQUENCES, noteDeletionConfirmed, takeDeletionNote } from "../src/session/account-deletion.ts";

/** Somebody deleting their account reads two things: what it will do, and,
 *  if it is refused, what to do about it. Both have to be right before the
 *  button is pressed, because afterwards there is nothing to take back. */

test("the confirmation is the account's own address, in any case", () => {
    assert.equal(confirmsAccount("Test+Asha@Example.com ", "test+asha@example.com"), true);
    assert.equal(confirmsAccount("test+asha@example.co", "test+asha@example.com"), false);
    assert.equal(confirmsAccount("DELETE", "test+asha@example.com"), false);
    // An account whose address is not known yet cannot be confirmed by typing nothing.
    assert.equal(confirmsAccount("", ""), false);
});

test("what deleting does is said before it happens, including what stays", () => {
    const said = DELETION_CONSEQUENCES.join(" ");
    assert.match(said, /signed out everywhere/);
    assert.match(said, /“Deleted user”/);
    assert.match(said, /cannot be undone/);
});

test("the only owner of an organization is told which ones to hand on", () => {
    const refused = deletionRefusal({
        statusCode: 409,
        code: "ACCOUNT_DELETION_SOLE_OWNER",
        message: "Make someone else an owner of Acme and Beta first. Other people still work there.",
        details: { organizations: [{ id: "1", name: "Acme" }, { id: "2", name: "Beta" }] },
    });
    assert.deepEqual(refused.organizations, ["Acme", "Beta"]);
    assert.match(refused.message, /Make someone else an owner of Acme and Beta first/);
});

test("a billing refusal is shown in the words it came with", () => {
    const refused = deletionRefusal({
        statusCode: 409,
        code: "ACCOUNT_DELETION_ACTIVE_SUBSCRIPTION",
        message: "Cancel your subscription before deleting your account.",
    });
    assert.equal(refused.message, "Cancel your subscription before deleting your account.");
});

test("an account that cannot be used is not announced as deleted", () => {
    // The same refusal answers an account that was switched off.
    const refused = deletionRefusal({ statusCode: 403, code: "ACCOUNT_INACTIVE" });
    assert.match(refused.message, /can no longer be used/);
    assert.doesNotMatch(refused.message, /has been deleted/);
});

test("an expired session is not mistaken for a deleted account", () => {
    const refused = deletionRefusal({ statusCode: 401, code: "UNAUTHORIZED", message: "Unauthorized" });
    assert.match(refused.message, /Sign in again/);
});

test("an answer that never came does not claim to know what happened", () => {
    for (const problem of [new TypeError("Failed to fetch"), { statusCode: 500, message: "Internal" }, null]) {
        assert.match(deletionRefusal(problem).message, /couldn’t confirm/);
    }
});

test("the deletion page says an account is gone once, and only where this tab deleted it", () => {
    const notes = new Map<string, string>();
    const store = {
        getItem: (key: string) => notes.get(key) ?? null,
        setItem: (key: string, value: string) => void notes.set(key, value),
        removeItem: (key: string) => void notes.delete(key),
    };
    // Anybody can open the address; it proves nothing on its own.
    assert.equal(takeDeletionNote("?deleted=1", () => store), false);
    noteDeletionConfirmed(() => store);
    assert.equal(takeDeletionNote("", () => store), false);
    assert.equal(takeDeletionNote("?deleted=1", () => store), true);
    // Read once: coming back to the address later in this tab is an ordinary visit.
    assert.equal(takeDeletionNote("?deleted=1", () => store), false);
});

test("a browser that refuses storage altogether is shown the ordinary page", () => {
    const blocked = (): never => { throw new Error("SecurityError"); };
    noteDeletionConfirmed(blocked);
    assert.equal(takeDeletionNote("?deleted=1", blocked), false);
});
