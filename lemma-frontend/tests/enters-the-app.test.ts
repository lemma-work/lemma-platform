import test from "node:test";
import assert from "node:assert/strict";
import { entersTheApp } from "../src/session/auth-state.ts";

/** Which of the two pages a visitor to the root is owed. */

test("somebody the SDK knows is signed in goes to the workspace", () => {
    assert.equal(entersTheApp("authenticated", null, false), true);
});

test("a signed-out visitor is left on the page that explains the product", () => {
    assert.equal(entersTheApp("unauthenticated", false, false), false);
});

test("the SDK's no is not the API's no", () => {
    // The whole reason `direct` exists. Coming back from the auth site there is
    // no front token on this origin, so `doesSessionExist()` is false and the
    // SDK answers unauthenticated without making a request. Believing it showed
    // the marketing page to a signed-in person on every first load.
    assert.equal(entersTheApp("unauthenticated", true, false), true);
});

test("nothing is decided from an answer nobody has asked for", () => {
    // null is "not asked", which is not "no" — it must not read as one.
    assert.equal(entersTheApp("unauthenticated", null, false), false);
    assert.equal(entersTheApp("loading", null, false), false);
});

test("sample mode has no session to be in, however loudly the SDK agrees", () => {
    assert.equal(entersTheApp("authenticated", true, true), false);
});

test("an unconfigured origin has nowhere to ask, so it never sends anyone on", () => {
    // The workspace would only meet them with the setup screen.
    assert.equal(entersTheApp("authenticated", true, false, false), false);
});
