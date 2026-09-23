import assert from "node:assert/strict";
import { test } from "node:test";
import { INITIAL_TOUR, tourReducer } from "../src/app/(marketing)/tour-state.ts";

test("Library remains selected while scrolling within and beyond a guided step", () => {
    let state = tourReducer(INITIAL_TOUR, { type: "scroll", step: 0 });
    state = tourReducer(state, { type: "tab", tab: 2 });
    state = tourReducer(state, { type: "scroll", step: 0 });
    state = tourReducer(state, { type: "scroll", step: 3 });
    assert.equal(state.tab, 2);
    assert.equal(state.mode, "exploring");
    state = tourReducer(state, { type: "resume" });
    assert.equal(state.tab, 1);
    assert.equal(state.mode, "guided");
    state = tourReducer(state, { type: "scroll", step: 4 });
    assert.equal(state.step, 4);
    assert.equal(state.tab, 0);
});

test("changing teammates preserves the open tab and transfers control to the visitor", () => {
    const profile = tourReducer(INITIAL_TOUR, { type: "step", step: 2 });
    const research = tourReducer(profile, { type: "teammate", who: 2 });
    const scrolled = tourReducer(research, { type: "scroll", step: 0 });
    assert.equal(scrolled.who, 2);
    assert.equal(scrolled.tab, 3);
    assert.equal(scrolled.mode, "exploring");
});

test("repeated scroll positions do not update the selected panel", () => {
    const state = tourReducer(INITIAL_TOUR, { type: "scroll", step: 1 });
    assert.equal(tourReducer(state, { type: "scroll", step: 1 }), state);
});
