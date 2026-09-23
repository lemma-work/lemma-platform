import test from "node:test";
import assert from "node:assert/strict";
import { VoiceEvents } from "../src/call/voice-events.ts";
import type { VoiceEvent } from "../src/call/routing.ts";
const event = (id: string, overrides: Partial<VoiceEvent> = {}): VoiceEvent => ({ id, conversationId: "research", kind: "snapshot", text: "Three companies found", speak: true, ...overrides });

test("a spoken result is delivered once, never also injected as quiet context", () => {
    const queue = new VoiceEvents();
    assert.equal(queue.receive(event("one")), null);
    assert.match(queue.take()!, /Three companies found/);
    assert.equal(queue.take(), null);
    assert.equal(queue.receive(event("one")), null);
    assert.equal(queue.waiting, false);
});

test("quiet events never schedule a second spoken response", () => {
    const queue = new VoiceEvents();
    assert.match(queue.receive(event("one", { speak: false }))!, /Three companies found/);
    assert.equal(queue.waiting, false);
});

test("queued snapshots are replaced by current state and separate updates share one response", () => {
    const queue = new VoiceEvents();
    queue.receive(event("one", { text: "old state" }));
    queue.receive(event("two", { text: "new state" }));
    queue.receive(event("three", { conversationId: "website", kind: "completed", text: "website done" }));
    const response = queue.take()!;
    assert.doesNotMatch(response, /old state/);
    assert.match(response, /new state/); assert.match(response, /website done/);
    assert.equal(queue.waiting, false);
});

test("clarification is a direct speech instruction and takes precedence over background updates", () => {
    const queue = new VoiceEvents();
    queue.receive(event("result", { kind: "completed" }));
    queue.receive(event("question", { kind: "clarify", conversationId: null, text: "Which report?" }));
    assert.equal(queue.needsClarification, true);
    assert.match(queue.take()!, /^Ask the caller one short clarifying question now/);
    assert.equal(queue.needsClarification, false);
    assert.equal(queue.waiting, true);
});

test("results from an earlier request become knowledge instead of an unsolicited turn", () => {
    const queue = new VoiceEvents();
    queue.beginRequest("first");
    queue.receive(event("result", { responseTo: "first" }));
    assert.deepEqual(queue.beginRequest("second"), ["Three companies found"]);
    assert.equal(queue.take(), null);
    assert.match(queue.receive(event("late", { responseTo: "first" }))!, /Three companies/);
    assert.equal(queue.waiting, false);
    queue.receive(event("current", { responseTo: "second" }));
    assert.match(queue.take()!, /Three companies/);
});
