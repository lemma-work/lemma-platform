import test from "node:test";
import assert from "node:assert/strict";
import { splitSettled } from "../src/thread/stream-blocks.ts";

/** Where a reply still streaming can be cut, so only its last block is parsed
 *  again as tokens arrive. Every cut has to render exactly as the whole reply
 *  will once it lands; a wrong one shows the reply differently mid-stream. */

function joined(text: string): string {
    const { settled, tail } = splitSettled(text);
    return settled.join("") + tail;
}

test("finished paragraphs are settled and the one being written is not", () => {
    const text = "First point.\n\nSecond point.\n\nThird is still go";
    const { settled, tail } = splitSettled(text);

    assert.deepEqual(settled, ["First point.\n\n", "Second point.\n\n"]);
    assert.equal(tail, "Third is still go");
    assert.equal(joined(text), text);
});

test("a block is not settled until something follows it", () => {
    // The blank line alone does not say the next line is a new block: it may
    // be the continuation of a list item, or more of the same list.
    assert.deepEqual(splitSettled("Only paragraph.\n\n").settled, []);
    assert.deepEqual(splitSettled("Only paragraph.\n\nNext").settled, ["Only paragraph.\n\n"]);
});

test("nothing inside a fenced code block is a place to cut", () => {
    const text = "Run this:\n\n```sh\nmake dev\n\nmake test\n```\n\nThen check";
    const { settled, tail } = splitSettled(text);

    assert.deepEqual(settled, ["Run this:\n\n", "```sh\nmake dev\n\nmake test\n```\n\n"]);
    assert.equal(tail, "Then check");
});

test("an unclosed fence keeps everything after it in the tail", () => {
    const text = "Here:\n\n```py\nprint(1)\n\nprint(2)\n";
    assert.equal(splitSettled(text).tail, "```py\nprint(1)\n\nprint(2)\n");
});

test("a loose list is one block", () => {
    // Cut between items, the halves would render as two tight lists and the
    // spacing would jump when the reply lands.
    const text = "- one\n\n- two\n\n  continued\n\n- three\n\nAfter the list";
    const { settled, tail } = splitSettled(text);

    assert.deepEqual(settled, ["- one\n\n- two\n\n  continued\n\n- three\n\n"]);
    assert.equal(tail, "After the list");
});

test("replies whose meaning reaches across blank lines are never cut", () => {
    for (const text of [
        "<details>\n\nHidden paragraph.\n\n</details>\n\nAfter",
        "See [the doc][1].\n\nMore text.\n\n[1]: https://example.com\n\nEnd",
        "A claim.[^1]\n\nMore.\n\n[^1]: The source.\n\nEnd",
    ]) {
        assert.deepEqual(splitSettled(text), { settled: [], tail: text });
    }
});
