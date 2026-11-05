import test from "node:test";
import assert from "node:assert/strict";
import { readTarget, unseenThreadMention, type ReadInputs } from "../src/channels/read-state.ts";

/** A channel is read as far as the newest line the reader has seen -- not
 *  by the page being open, and not past what is below the fold. */

const base: ReadInputs = { seenMax: 0, atEnd: true, newestShown: 12, lastSeq: 12, readSeq: 8, marked: 0 };

test("nothing seen yet marks nothing", () => {
    assert.equal(readTarget(base), null);
});

test("seeing the newest line at the bottom reads the channel through its newest line of all", () => {
    /* Line 14 is a thread reply: counted, not shown. */
    assert.equal(readTarget({ ...base, seenMax: 12, lastSeq: 14 }), 14);
});

test("reading back marks only as far as what was on screen, never the newest", () => {
    assert.equal(readTarget({ ...base, seenMax: 10, atEnd: false, lastSeq: 14 }), 10);
    assert.equal(readTarget({ ...base, seenMax: 6, atEnd: false }), null, "older than what was read already");
});

test("scrolled up with the newest line half in view is not at the bottom: replies past it stay unread", () => {
    assert.equal(readTarget({ ...base, seenMax: 12, atEnd: false, lastSeq: 14 }), 12);
});

test("a line is marked once: what this view asked for already is not asked again", () => {
    assert.equal(readTarget({ ...base, seenMax: 12, marked: 12 }), null);
    assert.equal(readTarget({ ...base, seenMax: 13, newestShown: 13, lastSeq: 13, marked: 12 }), 13);
});

test("the read position never goes back, even when another tab read further", () => {
    assert.equal(readTarget({ ...base, seenMax: 12, readSeq: 20, lastSeq: 12 }), null);
});

test("reading to the end stops short of a reply naming the reader in a thread they have not opened", () => {
    /* Lines 13 and 15 are replies; 13 names the reader, in a closed thread. */
    assert.equal(readTarget({ ...base, seenMax: 12, lastSeq: 15, heldBack: 13 }), 12);
    assert.equal(readTarget({ ...base, seenMax: 12, lastSeq: 15, heldBack: 14 }), 13);
    assert.equal(readTarget({ ...base, seenMax: 12, lastSeq: 15, heldBack: null }), 15);
    const mentions = [
        { channelId: "c1", seq: 14, threadRootId: "root-a" },
        { channelId: "c1", seq: 13, threadRootId: "root-b" },
        { channelId: "c1", seq: 11, threadRootId: null },
        { channelId: "c2", seq: 2, threadRootId: "root-c" },
    ];
    assert.equal(unseenThreadMention(mentions, "c1", null), 13);
    assert.equal(unseenThreadMention(mentions, "c1", "root-b"), 14, "the open thread's mention is on screen");
    assert.equal(unseenThreadMention(mentions, "c3", null), null);
});
