import test from "node:test";
import assert from "node:assert/strict";
import { buildTurns, type RawMessage } from "../src/thread/turns.ts";
import { keepUnchanged } from "../src/thread/turn-identity.ts";

/** Turns that did not change keep the object already on screen, so a memoised
 *  row skips drawing again when a message lands somewhere else. */

const at = (seconds: number) => new Date(1_700_000_000_000 + seconds * 1000).toISOString();
const conversation: RawMessage[] = [
    { id: "u1", role: "user", text: "First question", sequence: 1, created_at: at(1) },
    { id: "a1", role: "assistant", text: "First answer", sequence: 2, created_at: at(2) },
    { id: "u2", role: "user", text: "Second question", sequence: 3, created_at: at(3) },
];

test("a message landing in the last turn leaves the earlier ones as they were", () => {
    const before = buildTurns(conversation);
    const after = keepUnchanged(before, buildTurns([
        ...conversation,
        { id: "a2", role: "assistant", text: "Second answer", sequence: 4, created_at: at(4) },
    ]));

    assert.equal(after[0], before[0], "the first turn was redrawn for a message that is not in it");
    assert.notEqual(after[1], before[1]);
    assert.equal(after[1].items.length, 1);
});

test("rebuilding the same conversation hands back the same list", () => {
    const before = buildTurns(conversation);
    assert.equal(keepUnchanged(before, buildTurns(conversation)), before);
});

test("a change anywhere inside a turn counts", () => {
    // An approval answered, a card's result arriving: a fingerprint of a few
    // fields would miss these and leave the row showing the old state.
    const before = buildTurns(conversation);
    const edited = conversation.map((message) => message.id === "a1" ? { ...message, text: "First answer, revised" } : message);
    const after = keepUnchanged(before, buildTurns(edited));

    assert.notEqual(after[0], before[0]);
    assert.equal(after[1], before[1]);
});
