import test from "node:test";
import assert from "node:assert/strict";
import { nextStep, stepsFor } from "../src/arrival/steps.ts";
import { JOBS, OTHER_LIMIT, asksWorkOn, stillAsksWork, workAnswer, type JobId } from "../src/arrival/work.ts";

/** Every door walks the same steps after its own front: an organization,
 *  then (for one that already has people) its Lemmas to join, then the job
 *  question, then whatever the plugin step adds. */

test("a new organization is shown, then the job question", () => {
    assert.deepEqual(stepsFor("new_org", { asksWork: true, hasPlugins: false }), ["workspace", "work"]);
});

test("coming into an organization with people in it shows its Lemmas to join", () => {
    for (const entry of ["invitation", "domain_join", "existing", "surface_join"] as const) {
        assert.deepEqual(stepsFor(entry, { asksWork: true, hasPlugins: false }), ["workspace", "lemmas", "work"]);
    }
});

test("the job question is left out for somebody who already answered, and plugins follow it when there are any", () => {
    assert.deepEqual(stepsFor("new_org", { asksWork: false, hasPlugins: false }), ["workspace"]);
    assert.deepEqual(stepsFor("new_org", { asksWork: true, hasPlugins: true }), ["workspace", "work", "plugins"]);
});

test("the step after the last is none", () => {
    const steps = stepsFor("invitation", { asksWork: true, hasPlugins: false });
    assert.equal(nextStep(steps, "workspace"), "lemmas");
    assert.equal(nextStep(steps, "lemmas"), "work");
    assert.equal(nextStep(steps, "work"), null);
});

test("nine jobs, in the order they are shown", () => {
    assert.deepEqual(JOBS.map((job) => job.id), [
        "SALES", "MARKETING", "PRODUCT", "FOUNDER", "OPERATIONS", "HR", "PROJECT_MANAGEMENT", "ENGINEERING", "SUPPORT",
    ]);
});

test("an answer keeps the screen's order and the person's own words only when there are some", () => {
    const picked = new Set<JobId>(["SUPPORT", "SALES"]);
    assert.deepEqual(workAnswer(picked, "  "), { jobs: ["SALES", "SUPPORT"], other: null });
    assert.deepEqual(workAnswer(new Set(), "  run a bakery "), { jobs: [], other: "run a bakery" });
    assert.equal(workAnswer(new Set(), "x".repeat(OTHER_LIMIT + 40)).other?.length, OTHER_LIMIT);
});

test("skipping is an answer: nothing picked, nothing written", () => {
    assert.deepEqual(workAnswer(new Set(), ""), { jobs: [], other: null });
});

const DAY = 24 * 60 * 60 * 1000;
const NOW = Date.parse("2026-11-17T12:00:00Z");

test("a new account that has not answered is asked", () => {
    assert.equal(stillAsksWork({ work: null, created_at: new Date(NOW - 2 * DAY).toISOString() }, NOW), true);
});

test("an answer of any kind, skipping included, ends the question", () => {
    assert.equal(stillAsksWork({ work: { jobs: [], other: null, answered_at: "2026-11-16T00:00:00Z" }, created_at: new Date(NOW - DAY).toISOString() }, NOW), false);
});

test("people who were here long before the question are not stopped by it", () => {
    assert.equal(stillAsksWork({ work: null, created_at: new Date(NOW - 60 * DAY).toISOString() }, NOW), false);
});

test("a server that does not ask the question never stops anybody", () => {
    assert.equal(stillAsksWork({ created_at: new Date(NOW - DAY).toISOString() }, NOW), false);
    assert.equal(stillAsksWork(undefined, NOW), false);
    assert.equal(stillAsksWork({ work: null, created_at: "not a date" }, NOW), false);
});

test("somebody who missed the question is asked it on Home, never over a link they followed", () => {
    assert.equal(asksWorkOn("today"), true);
    // A chat opened from WhatsApp, an app from an email, a Lemma a colleague
    // sent: what they came for opens, and the question waits for Home.
    for (const view of ["chats", "app", "outcome", "outcomes", "apps", "needs", "people"]) {
        assert.equal(asksWorkOn(view), false, view);
    }
});

test("every door asks the server for a first workspace the same way", async () => {
    const { firstWorkspaceWanted } = await import("../src/arrival/first-workspace.ts");
    assert.deepEqual(firstWorkspaceWanted({ local: false }), {});
    assert.deepEqual(firstWorkspaceWanted({ own: true, local: false }), { ownOrganization: true });
    // On a local install nobody else can reach the server yet, so it is never
    // opened to a whole email domain -- from the web or a plugin's page.
    assert.deepEqual(firstWorkspaceWanted({ local: true }), { team: false });
    assert.deepEqual(firstWorkspaceWanted({ own: true, local: true }), { ownOrganization: true, team: false });
});
