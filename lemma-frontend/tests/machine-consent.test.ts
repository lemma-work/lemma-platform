import test from "node:test";
import assert from "node:assert/strict";
import { appIdFromSearch, consentMessage, finish } from "../src/machines/consent-model.ts";
import { DEFAULT_MACHINE_LABEL, machineChoices } from "../src/machines/machine-choices.ts";

const APP = "0b6c1d2e-3f40-4a5b-8c6d-7e8f9a0b1c2d";

test("only an app id is read from a consent link", () => {
    assert.equal(appIdFromSearch("?app=" + APP), APP);
    assert.equal(appIdFromSearch("?app=" + APP.toUpperCase()), APP);
    assert.equal(appIdFromSearch("?app=not-an-id"), null);
    assert.equal(appIdFromSearch("?name=Totally+Lemma"), null);
    assert.equal(appIdFromSearch(""), null);
});

test("the opener hears the answer and the tab closes", () => {
    const heard: unknown[] = [];
    let closed = false;
    const opener = { postMessage: (message: unknown, origin: string) => heard.push([message, origin]) };
    const gone = finish(APP, true, { opener: opener as unknown as Window, close: () => { closed = true; } });
    assert.equal(gone, true);
    assert.equal(closed, true);
    assert.deepEqual(heard, [[consentMessage(APP, true), "*"]]);
    assert.equal(consentMessage(APP, true).type, "lemma:machine-consent");
});

test("a tab nobody opened stays, to say it can be closed", () => {
    let closed = false;
    assert.equal(finish(APP, false, { opener: null, close: () => { closed = true; } }), false);
    assert.equal(closed, false);
});

test("an agent can run on the default machine or on one of the pod's", () => {
    const choices = machineChoices([
        { name: "ci", display_name: "Build box", kind: "pod", status: "running" },
        { name: "gone", display_name: "gone", kind: "pod", status: "deleted" },
        { name: "laptop", display_name: "laptop", kind: "connected", status: "offline" },
    ], "");
    assert.deepEqual(choices, [
        { value: "", label: DEFAULT_MACHINE_LABEL },
        { value: "pod:ci", label: "Build box (ci)" },
    ]);
});

test("a machine the agent names but the pod no longer has is shown, not hidden", () => {
    const choices = machineChoices([], "pod:old");
    assert.deepEqual(choices.at(-1), { value: "pod:old", label: "pod:old (not found)" });
});
