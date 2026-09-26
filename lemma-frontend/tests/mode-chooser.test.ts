import test from "node:test";
import assert from "node:assert/strict";
import { returnToModeChooser } from "../src/desktop/mode-chooser.ts";

/* Cancel on the hosted sign-in asks the shell to go back to the chooser, and
   falls back to its own page whenever it cannot. */

test("in the app, Cancel asks the shell to go back to the chooser", async () => {
    const asked: string[] = [];
    const went = await returnToModeChooser(async (command) => { asked.push(command); return undefined as never; }, () => true);
    assert.equal(went, true);
    assert.deepEqual(asked, ["return_to_mode_chooser"]);
});

test("a shell that refuses, or is too old to know the command, leaves the fallback page", async () => {
    const went = await returnToModeChooser(async () => { throw new Error("not allowed"); }, () => true);
    assert.equal(went, false);
});

test("a plain browser asks nothing", async () => {
    let asked = false;
    const went = await returnToModeChooser(async () => { asked = true; return undefined as never; }, () => false);
    assert.equal(went, false);
    assert.equal(asked, false);
});
