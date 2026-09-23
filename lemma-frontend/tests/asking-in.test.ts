import test from "node:test";
import assert from "node:assert/strict";
import { askerName, readJoinRequest } from "../src/data/joining.ts";
import { isForbidden, isMissing, isUnauthorized } from "../src/session/auth-state.ts";

/** Reading an ask, and telling the three refusals apart. Both of these decide
 *  what somebody standing outside a teammate is told, and the wrong answer to
 *  either sends them to wait for something that is not coming. */

test("an ask is read off the wire", () => {
    const request = readJoinRequest({
        id: "jr-9",
        pod_id: "marketing",
        status: "PENDING",
        user_name: "Tomas Ruiz",
        user_email: "tomas@acme.test",
        requested_at: "2026-09-18T09:12:00Z",
    });
    assert.deepEqual(request, {
        id: "jr-9",
        podId: "marketing",
        standing: "pending",
        name: "Tomas Ruiz",
        email: "tomas@acme.test",
        askedAt: "2026-09-18T09:12:00Z",
    });
});

test("the three standings are named in this app's words", () => {
    assert.equal(readJoinRequest({ id: "a", status: "PENDING" })?.standing, "pending");
    assert.equal(readJoinRequest({ id: "a", status: "APPROVED" })?.standing, "approved");
    // `REJECTED` on the wire; "refused" here, because nothing in this app
    // rejects anything — there is no endpoint that sets it.
    assert.equal(readJoinRequest({ id: "a", status: "REJECTED" })?.standing, "refused");
});

test("a status this build does not know reads as still waiting", () => {
    // The asymmetry is the point. Reading an unknown status as `approved`
    // tells somebody they are in, and they find out by walking into a wall.
    assert.equal(readJoinRequest({ id: "a", status: "ESCALATED" })?.standing, "pending");
    assert.equal(readJoinRequest({ id: "a" })?.standing, "pending");
});

test("`requested_at` wins over `created_at`, and either will do", () => {
    assert.equal(readJoinRequest({ id: "a", requested_at: "R", created_at: "C" })?.askedAt, "R");
    assert.equal(readJoinRequest({ id: "a", created_at: "C" })?.askedAt, "C");
    assert.equal(readJoinRequest({ id: "a" })?.askedAt, "");
});

test("nothing that is not an ask is read as one", () => {
    // An id is the whole test of whether a row can be acted on: admitting
    // somebody is a call that needs one.
    assert.equal(readJoinRequest(null), null);
    assert.equal(readJoinRequest("jr-9"), null);
    assert.equal(readJoinRequest({}), null);
    assert.equal(readJoinRequest({ status: "PENDING" }), null);
});

test("an ask with no name on it is still answerable", () => {
    // The platform fills `user_name` from a profile a brand-new account has
    // not written yet — which is exactly the account most likely to knock.
    const named = readJoinRequest({ id: "a", user_name: "Tomas Ruiz", user_email: "t@acme.test" })!;
    const mailed = readJoinRequest({ id: "a", user_email: "n.okafor@acme.test" })!;
    const blank = readJoinRequest({ id: "a" })!;
    assert.equal(askerName(named), "Tomas Ruiz");
    assert.equal(askerName(mailed), "n.okafor@acme.test");
    assert.equal(askerName(blank), "Somebody with no name set");
});

/** The three refusals. Each one is a different sentence to the person. */

test("404 is the only one that means there is no such teammate", () => {
    // This is what separates "ask to join" from "that link is wrong". The
    // platform answers a real request to a non-member and 404 only when the
    // pod is genuinely absent, so the distinction is load-bearing.
    assert.equal(isMissing({ statusCode: 404 }), true);
    assert.equal(isMissing({ name: "NotFoundError" }), true);
    assert.equal(isMissing({ statusCode: 403 }), false);
    assert.equal(isMissing({ statusCode: 401 }), false);
    assert.equal(isMissing(new Error("nope")), false);
    assert.equal(isMissing(null), false);
});

test("the three do not answer for each other", () => {
    const missing = { statusCode: 404 };
    assert.equal(isUnauthorized(missing), false);
    assert.equal(isForbidden(missing), false);
    // A 403 listing who is waiting is an ordinary member, not a fault — it is
    // what makes the queue draw nothing rather than an error.
    assert.equal(isForbidden({ statusCode: 403 }), true);
    assert.equal(isMissing({ statusCode: 403 }), false);
});
