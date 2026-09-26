import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { ownSettingsRow, readStatus } from "../src/desktop/agent-host.ts";

/** "Use my own skills and settings" is a choice about this computer's Agent
 *  Host, made per agent. It was only under This Mac → Coding agents; people
 *  add and manage agents on Models, so it is drawn there too -- beside this
 *  computer's own agents and no other computer's. */

test("the Models page draws the switch beside this computer's agents only", async () => {
    const source = await readFile(new URL("../src/org/models.tsx", import.meta.url), "utf8");
    assert.match(source, /: here\s*\?\s*<OwnSettingsSwitch harness=\{agent\.harness\} name=\{agent\.name\} \/>/);
});

test("the switch reads the host's list, and says why when it cannot", () => {
    const status = readStatus({ available: true, running: true, own_settings: ["codex"] });
    assert.deepEqual(ownSettingsRow(status, "codex"), { checked: true, blocked: null });
    assert.deepEqual(ownSettingsRow(status, "claude-code"), { checked: false, blocked: null });
    assert.match(ownSettingsRow(readStatus({ available: true }), "codex").blocked ?? "", /Update Lemma/);
    assert.ok(ownSettingsRow(null, "codex").blocked);
});
