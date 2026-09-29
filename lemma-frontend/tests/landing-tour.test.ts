import assert from "node:assert/strict";
import { test } from "node:test";
import { INITIAL_TOUR, tourReducer } from "../src/app/(marketing)/tour-state.ts";

test("exploring holds while scrolling within the step it started on", () => {
    let state = tourReducer(INITIAL_TOUR, { type: "scroll", step: 2 });
    state = tourReducer(state, { type: "explore" });
    state = tourReducer(state, { type: "scroll", step: 2 });
    assert.equal(state.mode, "exploring");
    assert.equal(state.step, 2);
});

test("scrolling into another step hands the workspace back to the tour", () => {
    let state = tourReducer(INITIAL_TOUR, { type: "scroll", step: 2 });
    state = tourReducer(state, { type: "explore" });
    state = tourReducer(state, { type: "scroll", step: 3 });
    assert.deepEqual(state, { mode: "guided", step: 3 });
});

test("choosing a step resumes the tour even on the current step", () => {
    const exploring = tourReducer(tourReducer(INITIAL_TOUR, { type: "step", step: 1 }), { type: "explore" });
    assert.deepEqual(tourReducer(exploring, { type: "step", step: 1 }), { mode: "guided", step: 1 });
});

test("repeated scroll positions and repeated interactions do not update state", () => {
    const state = tourReducer(INITIAL_TOUR, { type: "scroll", step: 1 });
    assert.equal(tourReducer(state, { type: "scroll", step: 1 }), state);
    const exploring = tourReducer(state, { type: "explore" });
    assert.equal(tourReducer(exploring, { type: "explore" }), exploring);
});
