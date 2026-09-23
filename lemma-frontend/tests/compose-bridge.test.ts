import test from "node:test";
import assert from "node:assert/strict";
import {
    COMPOSE_MESSAGE_TYPE,
    COMPOSE_RESULT_MESSAGE_TYPE,
    acknowledge,
    isPodFrame,
    readComposeRequest,
    registerFrame,
} from "../src/thread/compose-bridge.ts";

/** A window, as far as this module is concerned: something to identify and
 *  something to answer on. */
function frame() {
    const sent: unknown[] = [];
    const view = { postMessage: (message: unknown) => { sent.push(message); } };
    return { view, sent };
}

function ask(source: unknown, data: unknown, origin = "https://widget.example") {
    return { source, data, origin } as unknown as MessageEvent;
}

test("a registered frame's request is read, trimmed and defaulted", () => {
    const { view } = frame();
    const drop = registerFrame(view as unknown as Window);
    const request = readComposeRequest(ask(view, {
        type: COMPOSE_MESSAGE_TYPE,
        id: "a1",
        text: "  why is Acme cooling?  ",
    }));
    assert.deepEqual(request, { id: "a1", text: "why is Acme cooling?", newConversation: false });
    drop();
});

test("a window the pod never registered is refused", () => {
    // The listener is on `window`, so every frame on the page — and anything
    // else posting to it — reaches the same handler. Registration is the only
    // thing separating a widget this pod is showing from a stranger.
    const stranger = frame();
    assert.equal(isPodFrame(stranger.view), false);
    assert.equal(
        readComposeRequest(ask(stranger.view, { type: COMPOSE_MESSAGE_TYPE, text: "post this" })),
        null,
    );
});

test("a frame stops being trusted once it is dropped", () => {
    const { view } = frame();
    registerFrame(view as unknown as Window)();
    assert.equal(readComposeRequest(ask(view, { type: COMPOSE_MESSAGE_TYPE, text: "hi" })), null);
});

test("anything that is not a compose request is ignored", () => {
    const { view } = frame();
    const drop = registerFrame(view as unknown as Window);
    const rejected = [
        { type: "lemma:preview-height", height: 320 },
        { type: COMPOSE_MESSAGE_TYPE, text: "   " },
        { type: COMPOSE_MESSAGE_TYPE, text: 42 },
        { type: COMPOSE_MESSAGE_TYPE },
        "lemma-compose",
        null,
    ];
    for (const data of rejected) assert.equal(readComposeRequest(ask(view, data)), null, String(data));
    drop();
});

test("text is capped, because nobody typed four thousand characters", () => {
    const { view } = frame();
    const drop = registerFrame(view as unknown as Window);
    const request = readComposeRequest(ask(view, {
        type: COMPOSE_MESSAGE_TYPE,
        text: "x".repeat(9000),
    }));
    assert.equal(request?.text.length, 4000);
    drop();
});

test("newConversation is only ever the boolean true", () => {
    const { view } = frame();
    const drop = registerFrame(view as unknown as Window);
    const truthy = readComposeRequest(ask(view, { type: COMPOSE_MESSAGE_TYPE, text: "a", newConversation: "yes" }));
    const real = readComposeRequest(ask(view, { type: COMPOSE_MESSAGE_TYPE, text: "a", newConversation: true }));
    assert.equal(truthy?.newConversation, false);
    assert.equal(real?.newConversation, true);
    drop();
});

test("the acknowledgement goes back to the frame that asked, at its own origin", () => {
    // A view nobody framed has no way to tell a click that worked from a click
    // that went nowhere. The reply is how it knows, so it is addressed rather
    // than broadcast.
    const { view, sent } = frame();
    const drop = registerFrame(view as unknown as Window);
    const event = ask(view, { type: COMPOSE_MESSAGE_TYPE, id: "a1", text: "hello" });
    const request = readComposeRequest(event);
    acknowledge(event, request!);
    assert.deepEqual(sent, [{ type: COMPOSE_RESULT_MESSAGE_TYPE, id: "a1", ok: true }]);
    drop();
});

test("an unregistered frame is not acknowledged either", () => {
    const { view, sent } = frame();
    acknowledge(ask(view, {}), { id: "a1", text: "hello", newConversation: false });
    assert.deepEqual(sent, []);
});
