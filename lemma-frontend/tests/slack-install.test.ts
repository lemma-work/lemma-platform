import test from "node:test";
import assert from "node:assert/strict";
import { installStep, installedNote, once } from "../src/slack/install.ts";

const ACME = { id: "org-1", name: "Acme" };
const BETA = { id: "org-2", name: "Beta" };

test("nothing is offered until both answers are in, and nothing is made where Slack isn't set up", () => {
    assert.deepEqual(installStep(undefined, [ACME]), { kind: "loading" });
    assert.deepEqual(installStep(true, undefined), { kind: "loading" });
    // Not set up here: even somebody with no organization is not given one.
    assert.deepEqual(installStep(false, []), { kind: "unavailable" });
    assert.deepEqual(installStep(false, [ACME]), { kind: "unavailable" });
});

test("a newcomer gets an organization on the click; a member of several picks one", () => {
    assert.deepEqual(installStep(true, []), { kind: "first" });
    assert.deepEqual(installStep(true, [ACME]), { kind: "choose", orgs: [ACME], picker: false });
    // Never the oldest membership silently: with a choice, the person makes it.
    assert.deepEqual(installStep(true, [ACME, BETA]), { kind: "choose", orgs: [ACME, BETA], picker: true });
});

test("a start runs once however often it is asked, and again after a failure", async () => {
    let calls = 0;
    const start = once(async () => {
        calls += 1;
        return "https://slack.com/oauth/v2/authorize";
    });
    await Promise.all([start(), start()]);
    assert.equal(calls, 1);

    let attempts = 0;
    const flaky = once(async () => {
        attempts += 1;
        if (attempts === 1) throw new Error("no");
        return "ok";
    });
    await assert.rejects(flaky());
    assert.equal(await flaky(), "ok");
    assert.equal(attempts, 2);
});

test("the page Slack sends people back to says only our own words", () => {
    const outcome = (connect: string, code: string | null = null) => ({
        connect,
        connector: "slack",
        account: null,
        code,
        reason: "Your account is suspended, call +1 555 0100",
    });
    const connected = installedNote(outcome("connected"));
    assert.equal(connected.ok, true);
    // Not every path sends a message (a re-add, a grant finished in another
    // browser): the note points at what is always there.
    assert.match(connected.lead, /Home tab/);
    assert.doesNotMatch(connected.lead, /in a moment|will message you/);
    const cancelled = installedNote(outcome("error", "OAUTH_PROVIDER_ERROR"));
    assert.equal(cancelled.ok, false);
    assert.match(cancelled.lead, /Slack didn't add Lemma/);
    // A crafted link's reason, and an unknown code, never reach the page.
    for (const note of [cancelled, installedNote(outcome("error", "SOMETHING_ELSE")), installedNote(outcome("error"))]) {
        assert.doesNotMatch(note.lead, /suspended|555/);
    }
    assert.equal(installedNote(null).ok, false);
});
