import { test } from "node:test";
import assert from "node:assert/strict";
import { frameState } from "@/thread/frame-state";

const base = { loaded: false, reported: null, unreported: false, stalled: false, expanded: false, full: false, ceiling: 480 };

test("a frame that has not loaded says it is loading, and holds space for it", () => {
    const state = frameState(base);
    assert.equal(state.show, "waiting");
    assert.ok(state.height > 0);
});

test("a frame that never loads stops claiming to be on its way", () => {
    /* The regression: readiness needed `load`, and the wait for a height did
       not begin until `load` had fired, so a refused navigation left this on
       "Loading…" with nothing able to end it. */
    const state = frameState({ ...base, stalled: true });
    assert.equal(state.show, "stalled");
    assert.equal(state.height, 0, "a frame with nothing coming holds no space");
});

test("a slow frame that does load is never called stalled", () => {
    const late = frameState({ ...base, stalled: true, loaded: true, reported: 640 });
    assert.equal(late.show, "content");
    assert.equal(late.height, 480, "held to the ceiling until it is expanded");
});

test("loaded and quiet about its height still resolves, once it has had long enough", () => {
    assert.equal(frameState({ ...base, loaded: true }).show, "waiting");
    assert.equal(frameState({ ...base, loaded: true, unreported: true }).show, "content");
});

test("expanded and full take the reported height whole", () => {
    assert.equal(frameState({ ...base, loaded: true, reported: 900, expanded: true }).height, 900);
    assert.equal(frameState({ ...base, loaded: true, reported: 900, full: true }).height, 900);
    assert.equal(frameState({ ...base, loaded: true, reported: 900 }).height, 480);
});
