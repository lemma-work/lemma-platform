import test from "node:test";
import assert from "node:assert/strict";
import { transcriptState } from "../src/thread/transcript-state.ts";

const initial = { loading: false, hasTurns: false, hasStreamingText: false, error: null };

test("saved history never shows an empty welcome before the read completes", () => {
    assert.equal(transcriptState({ ...initial, loading: true }), "loading");
    assert.equal(transcriptState(initial), "empty");
});

test("background reads and failed refreshes preserve the transcript", () => {
    assert.equal(transcriptState({ ...initial, hasTurns: true, loading: true }), "content");
    assert.equal(transcriptState({ ...initial, hasTurns: true, error: "Unavailable" }), "content");
    assert.equal(transcriptState({ ...initial, hasStreamingText: true, loading: true }), "content");
});

test("a failed initial read stays distinct from an empty conversation and can retry", () => {
    assert.equal(transcriptState({ ...initial, error: "Unavailable" }), "error");
    assert.equal(transcriptState({ ...initial, error: "Unavailable", loading: true }), "loading");
    assert.equal(transcriptState({ ...initial, hasTurns: true }), "content");
});
