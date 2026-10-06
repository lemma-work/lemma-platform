import assert from "node:assert/strict";
import { test } from "node:test";
import { assignBulk, confirmLive, initialFeedback, isFeedback, keepSpinner, mergeSpinner, summarize, theme } from "../src/marketing/kit/model.ts";
import { previewSource } from "../src/marketing/preview-source.ts";
import { isMemoryPath } from "../src/thread/memory-notes.ts";

test("the board's totals are the canon every other screen quotes", () => {
    const totals = summarize(initialFeedback().themes);
    assert.equal(totals.reports, 222);
    assert.equal(totals.themes, 9);
    assert.equal(totals.overnight, 8);
    assert.deepEqual(totals.loop, { ok: 31, asked: 44 });
    assert.deepEqual(totals.unowned.map(item => item.id).sort(), ["bulk", "spin"]);
});

test("only Dev, the PR's author, can confirm a fix is live", () => {
    const state = initialFeedback();
    assert.equal(confirmLive(state), state);
    const live = confirmLive({ ...state, view: "dev" });
    assert.equal(live.live, true);
    assert.equal(theme(live, "dates")?.column, "closed");
    assert.equal(confirmLive(live), live);
});

test("assigning the unowned theme gives it a ticket, once", () => {
    const assigned = assignBulk(initialFeedback(), "alex");
    assert.equal(theme(assigned, "bulk")?.owner, "alex");
    assert.equal(theme(assigned, "bulk")?.ticket, "LIN-257");
    assert.equal(assignBulk(assigned, "dev"), assigned);
});

test("merging the spinner theme folds its reports into the timeouts; keeping it gives it an owner", () => {
    const merged = mergeSpinner(initialFeedback());
    assert.equal(theme(merged, "spin"), undefined);
    assert.equal(theme(merged, "large")?.reports, 46);
    assert.equal(summarize(merged.themes).reports, 222);
    const kept = keepSpinner(initialFeedback());
    assert.equal(theme(kept, "spin")?.owner, "dev");
    assert.equal(mergeSpinner(kept), kept);
});

test("only state of this version's shape is restored", () => {
    assert.equal(isFeedback(initialFeedback()), true);
    assert.equal(isFeedback(JSON.parse(JSON.stringify(initialFeedback()))), true);
    assert.equal(isFeedback({ assets: [] }), false);
    assert.equal(isFeedback(null), false);
});

test("sample chats display useful widgets without the handoff narration", async () => {
    for (const id of ["remy", "june", "scout"]) {
        const conversation = await previewSource.getConversation(id);
        assert.equal(conversation.messages.length, 7);
        assert.equal(conversation.messages[2].tool_name, "display_resource");
        assert.equal((conversation.messages[2].tool_args as { type: string }).type, "WIDGET");
    }
    for (const id of ["kit", "remy", "june", "scout"]) {
        assert.doesNotMatch(JSON.stringify(await previewSource.getConversation(id)), /From Scout|To June|To Scout|To Kit/);
    }
});

test("Kit's chat shows its widgets and the app it built", async () => {
    const shown = (await previewSource.getConversation("kit")).messages
        .filter(message => message.tool_name === "display_resource")
        .map(message => (message.tool_args as { type: string }).type);
    assert.equal(shown.filter(type => type === "WIDGET").length, 3);
    assert.ok(shown.includes("APP"));
    assert.ok(shown.includes("WORKFLOW"));
});

test("each sample chat ends with a correction the teammate writes down", async () => {
    for (const id of ["remy", "june", "scout"]) {
        const [, , , told, , write] = (await previewSource.getConversation(id)).messages;
        assert.equal(told.role, "user");
        assert.equal(write.tool_name, "pod_write_file");
        // A write to memory is what draws "noted this" under the reply.
        assert.equal(isMemoryPath((write.tool_args as { path: string }).path), true);
    }
    // Kit's correction is the last turn, and fits the sample pane's first page
    // of eight messages, so it opens on the ask that made it.
    const messages = (await previewSource.getConversation("kit")).messages;
    const page = messages.slice(-8);
    assert.equal(page[0].role, "user");
    assert.equal(page.filter(message => message.role === "user").length, 1);
    const write = messages.findLast(message => message.tool_name === "pod_write_file");
    assert.ok(write);
    assert.equal(isMemoryPath((write.tool_args as { path: string }).path), true);
});
