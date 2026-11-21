import test from "node:test";
import assert from "node:assert/strict";
import { canTestRoute, googleSignInProblem } from "../src/org/provider-draft.ts";

test("a Google provider is checked on save, not by this computer's model lookup", () => {
    assert.equal(canTestRoute("google"), false);
    assert.equal(canTestRoute("openai"), true);
});

test("each Google host asks for its own way in", () => {
    assert.equal(googleSignInProblem("GEMINI_API", "", ""), "Paste a Gemini API key.");
    assert.equal(googleSignInProblem("GEMINI_API", "AIza-example", ""), null);
    assert.equal(googleSignInProblem("VERTEX_AI", "", ""), "Add the service account's key file.");
    assert.equal(googleSignInProblem("VERTEX_AI", "", "{\"type\":\"authorized_user\"}"), "That file isn't a Google Cloud service account key.");
    assert.equal(googleSignInProblem("VERTEX_AI", "", "not json"), "That file isn't a Google Cloud service account key.");
    assert.equal(googleSignInProblem("VERTEX_AI", "", "{\"type\":\"service_account\"}"), null);
});
