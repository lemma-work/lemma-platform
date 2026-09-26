import test from "node:test";
import assert from "node:assert/strict";
import { runFailure, transcriptState } from "../src/thread/transcript-state.ts";

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

test("a coding agent's failure is said as what happened, with where to look", () => {
    for (const recorded of [
        "No Agent Host received the run before its wait deadline",
        "Agent Host delivery could not be confirmed; the run was not repeated",
        "Agent Host did not emit a terminal event before the run deadline",
        "Agent Host reached terminal checkpoint FAILED without its required terminal event",
    ]) {
        const failure = runFailure(recorded);
        assert.equal(failure.codingAgents, true, recorded);
        assert.doesNotMatch(failure.text, /Agent Host|checkpoint|deadline/, recorded);
    }
    /* Anything else is already a sentence, and passes through. */
    assert.deepEqual(runFailure("Allowance exhausted"), { text: "Allowance exhausted", codingAgents: false });
    assert.deepEqual(runFailure(null), { text: "That run failed.", codingAgents: false });
});
