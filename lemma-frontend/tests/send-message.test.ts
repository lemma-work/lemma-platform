import test from "node:test";
import assert from "node:assert/strict";
import { sendToConversation, steerConversation } from "../src/thread/send-message.ts";

test("creates and selects the conversation before sending, without waiting for the stream to end", async () => {
    const events: string[] = [];
    let finish!: () => void;
    const stream = new Promise<void>(resolve => { finish = resolve; });
    const made = { id: "new-conversation" };
    const sending = sendToConversation("hello", {
        conversationId: null,
        create: async () => { events.push("create"); return made; },
        isActive: () => true,
        adopt: conversation => { events.push("adopt:" + conversation.id); },
        onCreated: conversation => { events.push("select:" + conversation.id); },
        send: async (text, id, known) => {
            assert.equal(text, "hello");
            assert.equal(known, made);
            events.push("send:" + id);
            await stream;
        },
    });
    await Promise.resolve();
    assert.deepEqual(events, ["create", "adopt:new-conversation", "select:new-conversation", "send:new-conversation"]);
    finish();
    await sending;
});

test("existing conversations and retries reuse the selected ID", async () => {
    let attempts = 0;
    const deps = {
        conversationId: "existing",
        create: async () => { throw new Error("must not create"); },
        isActive: () => true,
        adopt: () => assert.fail("must not adopt"),
        onCreated: () => assert.fail("must not reselect"),
        send: async (_text: string, id: string) => {
            assert.equal(id, "existing");
            if (++attempts === 1) throw new Error("send failed");
        },
    };
    await assert.rejects(sendToConversation("hello", deps), /send failed/);
    await sendToConversation("hello", deps);
    assert.equal(attempts, 2);
});

test("creation failures and navigation away never send a message", async () => {
    const deps = {
        conversationId: null,
        create: async () => { throw new Error("create failed"); },
        isActive: () => true,
        adopt: () => assert.fail("must not adopt"),
        onCreated: () => assert.fail("must not select"),
        send: async () => assert.fail("must not send"),
    };
    await assert.rejects(sendToConversation("hello", deps), /create failed/);
    await assert.rejects(sendToConversation("hello", {
        ...deps, create: async () => ({ id: "new" }), isActive: () => false,
    }), /Conversation changed/);
});

test("the pod's later id lands on a session that already holds it", async () => {
    /* The session cancels an in-flight stream whenever an id arrives from
       outside that differs from the one it holds, and skips the cancel when
       the id is the one it is already on. The pod delivers that id a render
       after the conversation is created — after the send has opened its
       stream. Adopting inside the send is what turns the pod's delivery into
       the second, harmless case. */
    let held: string | null = null;
    let streamAborted = false;
    let streaming = false;
    const mirror = (next: string | null) => {
        if (held === next) return;
        held = next;
        if (streaming) streamAborted = true;
    };

    let deliverFromPod!: () => void;
    const made = { id: "new-conversation" };
    await sendToConversation("hello", {
        conversationId: null,
        create: async () => made,
        isActive: () => true,
        adopt: conversation => mirror(conversation.id),
        /* The pod re-renders and pushes its id down whenever React gets to
           it, which is somewhere inside the send below. */
        onCreated: conversation => { deliverFromPod = () => mirror(conversation.id); },
        send: async () => {
            streaming = true;
            deliverFromPod();
            streaming = false;
        },
    });

    assert.equal(streamAborted, false, "the first message was cancelled by its own conversation id");
    assert.equal(held, made.id);
});

test("a steer whose attachment fails to upload says so and keeps the draft", async () => {
    const reported: string[] = [];
    const appended: string[] = [];
    let cleared = false;
    await assert.rejects(
        steerConversation("look at this", "c1", {
            putFiles: async () => { throw new Error("report.pdf is too large"); },
            append: async (_id, content) => { appended.push(content); },
            clearAttachments: () => { cleared = true; },
            restoreAttachments: () => undefined,
            report: message => { reported.push(message); },
        }),
        /too large/,
    );
    assert.deepEqual(reported, ["report.pdf is too large"]);
    assert.deepEqual(appended, [], "nothing is sent without its files");
    assert.equal(cleared, false, "the chips stay for a retry");
});

test("a steer whose append fails hands the uploaded files back and says why", async () => {
    const reported: string[] = [];
    let restored: string[] = [];
    await assert.rejects(
        steerConversation("and this", "c1", {
            putFiles: async (_id, text) => ({ content: text + "\n[report.pdf]", settled: ["report.pdf"] }),
            append: async () => { throw "offline"; },
            clearAttachments: () => undefined,
            restoreAttachments: settled => { restored = settled; },
            report: message => { reported.push(message); },
        }),
    );
    assert.deepEqual(restored, ["report.pdf"]);
    assert.deepEqual(reported, ["That did not send."]);
});

test("a steer that goes appends what the upload produced", async () => {
    const appended: string[] = [];
    await steerConversation("see attached", "c1", {
        putFiles: async (_id, text) => ({ content: text + "\n[a.png]", settled: [] }),
        append: async (id, content) => { appended.push(id + ":" + content); },
        clearAttachments: () => undefined,
        restoreAttachments: () => undefined,
        report: () => assert.fail("nothing to report"),
    });
    assert.deepEqual(appended, ["c1:see attached\n[a.png]"]);
});
