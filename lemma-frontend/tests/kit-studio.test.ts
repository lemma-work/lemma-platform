import assert from "node:assert/strict";
import { test } from "node:test";
import { initialStudio, editAsset, saveAsset, approveAsset, blockers, isDirty } from "../src/marketing/kit/model.ts";
import { previewSource } from "../src/marketing/preview-source.ts";

test("editing an approved asset requires a saved new revision and fresh approval", () => {
    let state = approveAsset(initialStudio(), "landing");
    assert.equal(state.assets[0].review, "Approved");
    state = editAsset(state, "landing", { title: "A clearer headline" });
    assert.equal(state.assets[0].review, "Needs review");
    assert.equal(isDirty(state.assets[0]), true);
    assert.equal(approveAsset(state, "landing"), state);
    const oldCopy = state.assets[0].versions[1].copy.title;
    state = saveAsset(state, "landing");
    assert.equal(state.assets[0].versions.at(-1)?.number, 4);
    assert.equal(state.assets[0].versions[1].copy.title, oldCopy);
    assert.equal(isDirty(state.assets[0]), false);
    state = approveAsset(state, "landing");
    assert.equal(state.assets[0].review, "Approved");
    assert.match(state.activity[0], /v4/);
});

test("customer attribution and unfinished frames block approval", () => {
    const state = initialStudio();
    assert.equal(approveAsset(state, "story"), state);
    assert.equal(approveAsset(state, "storyboard"), state);
    assert.equal(blockers({ ...state, anonymous: true }, state.assets[3]).length, 0);
    assert.equal(approveAsset({ ...state, anonymous: true }, "story").assets[3].review, "Approved");
    assert.equal(approveAsset({ ...state, shots: [true, true, true] }, "storyboard").assets[2].review, "Approved");
});

test("sample chats display useful widgets without the handoff narration", async () => {
    for (const id of ["kit", "remy", "june", "scout"]) {
        const conversation = await previewSource.getConversation(id);
        assert.equal(conversation.messages.length, 3);
        assert.equal(conversation.messages[2].tool_name, "display_resource");
        assert.equal((conversation.messages[2].tool_args as { type: string }).type, "WIDGET");
        assert.doesNotMatch(JSON.stringify(conversation), /From Scout|To June|To Scout|To Kit/);
    }
});
