import test from "node:test";
import assert from "node:assert/strict";
import { headingTo } from "../src/auth/heading.ts";

/** "Then on to …" under a sign-in's title: where it goes next, in words. */

const origin = "https://app.example";

test("a link to a Lemma names it", () => {
    assert.equal(headingTo({ destination: "/t/marketing/library", origin }), "Marketing");
    assert.equal(headingTo({ destination: "/l/marketing/apps", origin }), "Marketing");
});

test("a Lemma whose id is not a name is still somewhere, not an address", () => {
    assert.equal(headingTo({ destination: "/t/a3f9c1e2-4b5d-11ee-be56-0242ac120002/conversation", origin }), "that Lemma");
});

test("the default lands where it always does, and says nothing", () => {
    for (const destination of [null, "/", "/home", "/t", "/o/"]) {
        assert.equal(headingTo({ destination, origin }), null, String(destination));
    }
});

test("somewhere else in the app is the workspace", () => {
    assert.equal(headingTo({ destination: "/settings/billing", origin }), "your workspace");
});

test("an invitation, a template and Slack say what they are", () => {
    assert.equal(headingTo({ destination: "/invitations/0f8fad5b-d9cb-469f-a165-70867728950e/accept", origin }), "your invitation");
    assert.equal(headingTo({ destination: "/import/github/acme/crm-kit", origin }), "adding crm-kit from GitHub");
    assert.equal(headingTo({ destination: "/slack/install", origin }), "adding Lemma to Slack");
});

test("whoever this browser is signing in for comes first", () => {
    assert.equal(headingTo({ destination: "/t/marketing", origin, desktop: true }), "Lemma Desktop");
    assert.equal(headingTo({ destination: null, origin, cli: true }), "the Lemma CLI");
    assert.equal(headingTo({ destination: null, origin, consent: true }), "connecting an app");
});

test("an app on its own host is named as an app, never as its address", () => {
    assert.equal(headingTo({ destination: "https://crm.apps.example/deals", origin, appsSuffix: ".apps.example" }), "the app you were opening");
    assert.equal(headingTo({ destination: "https://elsewhere.example/", origin, appsSuffix: ".apps.example" }), null);
});
