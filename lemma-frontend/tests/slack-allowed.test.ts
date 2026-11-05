import test from "node:test";
import assert from "node:assert/strict";
import { allowedRefusal } from "../src/slack/allowed-outcome.ts";

test("an Allow that went through says nothing went wrong", () => {
    assert.equal(allowedRefusal("?connect=connected&account=a1"), null);
    assert.equal(allowedRefusal(""), null);
});

test("a reason carried by the link is never shown back", () => {
    // Anybody can send a link to this page with any reason in it.
    const forged = "?connect=error&reason=" + encodeURIComponent("Your workspace is suspended. Call +1 555 0100.");
    const said = allowedRefusal(forged);
    assert.ok(said);
    assert.doesNotMatch(said, /suspended|555/);
});

test("each refusal is said in Lemma's own fixed words", () => {
    assert.equal(allowedRefusal("?connect=error&code=OAUTH_PROVIDER_ERROR"), "Slack didn't allow the app.");
    assert.equal(allowedRefusal("?connect=error&code=SOMETHING_ELSE"), "Lemma couldn't finish adding the app.");
    assert.equal(allowedRefusal("?connect=error"), "Lemma couldn't finish adding the app.");
});
