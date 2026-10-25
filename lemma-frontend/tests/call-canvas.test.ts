import test from "node:test";
import assert from "node:assert/strict";
import { canvasItemFrom } from "../src/call/canvas.ts";
import { callSocketUrl } from "../src/call/call-socket.ts";

const frame = (id: string, args: Record<string, unknown>, result: unknown = { success: true }) => ({
    conversation_id: "child-conversation",
    tool_call_id: id,
    tool_name: "display_resource",
    args,
    result,
});

test("a widget on the canvas is served by the conversation and tool call that showed it", () => {
    const item = canvasItemFrom(frame("t1", {
        type: "WIDGET",
        name: "Offsite plan",
        path: "/skills/lemma-widget/library/show/diagram.html",
        data: { mermaid: "flowchart LR; A-->B" },
    }));
    assert.ok(item);
    assert.equal(item.id, "t1");
    // A sub-agent's item is served from the sub-agent's conversation.
    assert.equal(item.conversationId, "child-conversation");
    assert.equal(item.resource.type, "WIDGET");
    assert.equal(item.resource.path, "/skills/lemma-widget/library/show/diagram.html");
    assert.equal(item.label, "Offsite plan");
});

test("a pod file, a table or an app goes on the canvas as itself", () => {
    assert.equal(canvasItemFrom(frame("t2", { type: "FILE", path: "/me/brochure.pdf" }))?.label, "brochure.pdf");
    assert.equal(canvasItemFrom(frame("t3", { type: "TABLE", name: "venues" }))?.resource.type, "TABLE");
});

test("something that is not a resource does not go on the canvas", () => {
    assert.equal(canvasItemFrom(frame("t4", { kind: "sculpture" })), null);
    assert.equal(canvasItemFrom({ ...frame("", { type: "FILE", path: "/me/a.pdf" }) }), null);
});

test("the call socket rides the API origin, with the token when there is one", () => {
    assert.equal(callSocketUrl("https://api.example.test/", "p 1", "c1", "tok"), "wss://api.example.test/pods/p%201/calls/c1/socket?access_token=tok");
    assert.equal(callSocketUrl("http://localhost:8710", "p", "c", null), "ws://localhost:8710/pods/p/calls/c/socket");
});
