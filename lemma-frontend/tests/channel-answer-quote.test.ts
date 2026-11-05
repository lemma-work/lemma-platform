import test from "node:test";
import assert from "node:assert/strict";
import { answeredOutOfPlace } from "../src/channels/timeline-model.ts";

const line = (id: string, extra: { replyToId?: string | null; threadRootId?: string | null } = {}) =>
    ({ id, replyToId: extra.replyToId ?? null, threadRootId: extra.threadRootId ?? null });

test("an answer right under its question needs no quote; one with a line between says what it answers", () => {
    const question = line("q");
    const between = line("b");
    const answer = line("a", { replyToId: "q" });
    const byId = new Map([question, between, answer].map((one) => [one.id, one]));
    assert.equal(answeredOutOfPlace(answer, question, byId), null, "right under it");
    assert.equal(answeredOutOfPlace(answer, between, byId), question, "somebody wrote in between");
});

test("an answer in a thread, or to a line not held, quotes nothing", () => {
    const question = line("q");
    const byId = new Map([[question.id, question]]);
    assert.equal(answeredOutOfPlace(line("a", { replyToId: "q", threadRootId: "q" }), undefined, byId), null);
    assert.equal(answeredOutOfPlace(line("a", { replyToId: "gone" }), undefined, byId), null);
    assert.equal(answeredOutOfPlace(line("a"), undefined, byId), null);
});
