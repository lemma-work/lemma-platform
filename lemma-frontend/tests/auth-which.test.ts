import test from "node:test";
import assert from "node:assert/strict";
import { screenFor } from "../src/auth/which.ts";

/** Every path here is a contract with something outside this app — an email
 *  the backend already sent, or a redirect URI registered with Google. */

test("the portal's own door is sign-in", () => {
    assert.equal(screenFor(undefined), "sign-in");
    assert.equal(screenFor([]), "sign-in");
    assert.equal(screenFor(["signin"]), "sign-in");
    assert.equal(screenFor(["login"]), "sign-in");
});

test("legacy signup links open account creation", () => {
    assert.equal(screenFor([], "?show=signup&redirect_uri=/t/example"), "sign-up");
    assert.equal(screenFor([], "?mode=signup"), "sign-up");
    assert.equal(screenFor(["reset-password"], "?show=signup"), "reset");
});

test("the paths the backend puts in emails resolve", () => {
    // `auth_website_base_path` + SuperTokens' conventions. Changing either of
    // these breaks links already in people's inboxes.
    assert.equal(screenFor(["reset-password"]), "reset");
    assert.equal(screenFor(["verify-email"]), "verify");
});

test("a provider comes back to callback, with its id in the path", () => {
    assert.equal(screenFor(["callback", "google"]), "callback");
    assert.equal(screenFor(["callback", "active-directory"]), "callback");
    // Bare `/callback` is not a provider returning; it is somebody wandering.
    assert.equal(screenFor(["callback"]), "unknown");
});

test("anything deeper than the grammar is unknown, not a near miss", () => {
    assert.equal(screenFor(["signup", "extra"]), "unknown");
    assert.equal(screenFor(["reset-password", "token"]), "unknown");
    assert.equal(screenFor(["callback", "google", "extra"]), "unknown");
    assert.equal(screenFor(["nonsense"]), "unknown");
});
