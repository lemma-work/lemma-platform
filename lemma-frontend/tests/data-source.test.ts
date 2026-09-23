import test from "node:test";
import assert from "node:assert/strict";
import { dataSource } from "../src/data/index.ts";

/** Which pod source a browser is allowed to ask for. */

test("live is what you get when nobody said otherwise", () => {
    assert.equal(dataSource(undefined, null, true), "live");
    assert.equal(dataSource(undefined, null, false), "live");
});

test("a deployment built for sample stays sample", () => {
    assert.equal(dataSource("sample", null, true), "sample");
});

test("in development a browser may flip itself", () => {
    // How you judge a screen with no session to be had.
    assert.equal(dataSource("live", "sample", false), "sample");
    assert.equal(dataSource("sample", "live", false), "live");
});

test("in production the browser does not get a vote", () => {
    // Sample mode passes straight through SessionGate, so an override that
    // worked in production would be a way past the sign-in door into something
    // that looks like the app.
    assert.equal(dataSource("live", "sample", true), "live");
});
