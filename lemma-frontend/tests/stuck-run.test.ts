import test from "node:test";
import assert from "node:assert/strict";
import { STUCK_AFTER_MS, runLooksStuck } from "../src/thread/stuck-run.ts";

test("a running conversation with no stream is stuck only after the wait", () => {
    assert.equal(runLooksStuck("running", false, STUCK_AFTER_MS - 1), false);
    assert.equal(runLooksStuck("running", false, STUCK_AFTER_MS), true);
});

test("a stream carrying the run, or a run that is not going, is never stuck", () => {
    assert.equal(runLooksStuck("running", true, STUCK_AFTER_MS * 10), false);
    for (const state of ["idle", "waiting", "failed"] as const) {
        assert.equal(runLooksStuck(state, false, STUCK_AFTER_MS * 10), false);
    }
});
