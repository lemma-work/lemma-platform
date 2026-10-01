import test from "node:test";
import assert from "node:assert/strict";
import { followMoves, recordMove } from "../src/docpages/moves.ts";

test("a card holding a renamed page's old path reads where it is now", () => {
    recordMove("pod-a", "/pages/Untitled.md", "/pages/Launch plan.md");
    recordMove("pod-a", "/pages/Launch plan.md", "/pages/Launch plan v2.md");

    assert.equal(followMoves("pod-a", "/pages/Untitled.md"), "/pages/Launch plan v2.md");
    assert.equal(followMoves("pod-a", "/pages/Launch plan.md"), "/pages/Launch plan v2.md");
});

test("a move is the pod's own: the same path in another pod stays put", () => {
    recordMove("pod-b", "/pages/Notes.md", "/pages/Standup notes.md");
    assert.equal(followMoves("pod-c", "/pages/Notes.md"), "/pages/Notes.md");
});

test("two moves that undo each other do not loop", () => {
    recordMove("pod-d", "/pages/A.md", "/pages/B.md");
    recordMove("pod-d", "/pages/B.md", "/pages/A.md");
    assert.ok(["/pages/A.md", "/pages/B.md"].includes(followMoves("pod-d", "/pages/A.md")));
});
