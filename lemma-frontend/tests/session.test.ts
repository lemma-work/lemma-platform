import test from "node:test";
import assert from "node:assert/strict";
import { isForbidden, isUnauthorized, retryTransient, sessionStatus, transientRetryDelay, TRANSIENT_RETRIES, unreachableRetryDelay } from "../src/session/auth-state.ts";

test("only a 401 means the server is saying 'not you'", () => {
    assert.equal(isUnauthorized({ statusCode: 401 }), true);
    assert.equal(isUnauthorized({ name: "UnauthorizedError" }), true);

    // A 403 is somebody signed in meeting a permission or an RLS denial. Reading
    // it as a dead session signs them out of an app they are entitled to use.
    assert.equal(isUnauthorized({ statusCode: 403 }), false);
    assert.equal(isUnauthorized({ statusCode: 500 }), false);
    assert.equal(isUnauthorized(new Error("Network request failed")), false);
    assert.equal(isUnauthorized(null), false);
    assert.equal(isUnauthorized("401"), false);
});

test("a 401 is recognised across a package boundary", () => {
    // Read structurally on purpose: a duplicated copy of the SDK anywhere in the
    // module graph makes `instanceof UnauthorizedError` quietly false, and the
    // session would then never end.
    class SomeoneElsesUnauthorizedError extends Error {
        name = "UnauthorizedError";
        statusCode = 401;
    }
    assert.equal(isUnauthorized(new SomeoneElsesUnauthorizedError()), true);
});

test("sample mode is never asked to sign in", () => {
    // It has no backend to be authenticated against, so every auth answer maps
    // to the same thing. Gating it would leave nothing to look at without a
    // session, which is the one job it has.
    assert.equal(sessionStatus("unauthenticated", true), "sample");
    assert.equal(sessionStatus("loading", true), "sample");
    assert.equal(sessionStatus("authenticated", true), "sample");
});

test("the three real answers keep their meaning", () => {
    assert.equal(sessionStatus("authenticated", false), "in");
    assert.equal(sessionStatus("unauthenticated", false), "out");
    assert.equal(sessionStatus("loading", false), "loading");
});

test("loading is not the same as signed out", () => {
    // The distinction is the whole reason these are separate states: showing
    // the door while the answer is still in flight is a sign-in screen flashed
    // at somebody who is already signed in.
    assert.notEqual(sessionStatus("loading", false), sessionStatus("unauthenticated", false));
});

test("no API origin is its own answer, not a signed-out one", () => {
    // With nowhere to ask, every other state is a claim about a person this
    // app has not established. "Signed out" is the worst of them: it offers a
    // sign-in button that cannot work, and reads as an account problem when it
    // is a missing environment variable.
    assert.equal(sessionStatus("unauthenticated", false, false), "unconfigured");
    assert.equal(sessionStatus("loading", false, false), "unconfigured");
    assert.equal(sessionStatus("authenticated", false, false), "unconfigured");
});

test("sample mode needs no origin to reach nothing at", () => {
    // It outranks `unconfigured` for the reason it outranks the door: the mode
    // exists to be looked at with no backend, so requiring one to configure
    // would take away the only screen available without a session.
    assert.equal(sessionStatus("unauthenticated", true, false), "sample");
    assert.equal(sessionStatus("loading", true, false), "sample");
});

test("a configured deployment is unchanged by the new state", () => {
    // The parameter defaults to true so existing callers keep their meaning;
    // this pins that the default and the explicit value agree.
    assert.equal(sessionStatus("authenticated", false, true), sessionStatus("authenticated", false));
    assert.equal(sessionStatus("unauthenticated", false, true), sessionStatus("unauthenticated", false));
    assert.equal(sessionStatus("loading", false, true), sessionStatus("loading", false));
});

test("a permission boundary is not a missing session", () => {
    // 401 and 403 arrive the same way and mean opposite things: one is "sign
    // in", the other is "you are signed in, this is not yours". Treating the
    // second as the first signs somebody out of an app they are entitled to use.
    assert.equal(isForbidden({ statusCode: 403 }), true);
    assert.equal(isForbidden({ name: "ForbiddenError" }), true);
    assert.equal(isForbidden({ statusCode: 401 }), false);
    assert.equal(isUnauthorized({ statusCode: 403 }), false);
    assert.equal(isForbidden(null), false);
    assert.equal(isForbidden("403"), false);
});

test("a query is retried through a server restart, never after a real answer", () => {
    assert.equal(retryTransient(0, new TypeError("Failed to fetch")), true);
    assert.equal(retryTransient(0, { statusCode: 503 }), true);
    assert.equal(retryTransient(0, { status: 502 }), true);
    assert.equal(retryTransient(0, { statusCode: 401 }), false);
    assert.equal(retryTransient(0, { statusCode: 404 }), false);
    assert.equal(retryTransient(0, { statusCode: 500 }), false, "a server error is an answer too");
    assert.equal(retryTransient(TRANSIENT_RETRIES, new TypeError("Failed to fetch")), false);
    assert.ok(transientRetryDelay(10) <= 8_000);
});

test("an API that did not answer is not a signed-out person", () => {
    // A server restarting under the page used to come back "out", and the page
    // left for the sign-in portal. Only a 401 means that now.
    assert.equal(sessionStatus("unreachable", false), "unreachable");
    assert.equal(sessionStatus("unreachable", true), "sample");
    assert.equal(sessionStatus("unreachable", false, false), "unconfigured");
});

test("the unreachable screen looks again quickly, then backs off to thirty seconds", () => {
    assert.deepEqual([0, 1, 2, 3, 4, 5, 9].map(unreachableRetryDelay), [1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000]);
});
