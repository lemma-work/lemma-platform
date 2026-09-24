import assert from "node:assert/strict";
import { test } from "node:test";
import { isPreviewPath, readTourStep, previewTabForStep } from "../src/marketing/preview-mode.ts";
import { addPreviewMember, previewSource } from "../src/marketing/preview-source.ts";

test("sample routing is restricted to the two dedicated preview documents", () => {
    for (const path of ["/demo/landing", "/demo/launch", "/demo/landing/"]) assert.equal(isPreviewPath(path), true);
    for (const path of ["/", "/t", "/t/marketing", "/t/marketing?demo=1", "/demo/landing/secret", "/demo/landing-other"]) assert.equal(isPreviewPath(path), false);
});

test("the tour bridge accepts only known steps and its own message type", () => {
    assert.equal(readTourStep({ type: "lemma-tour:step", step: 4 }), 4);
    assert.equal(readTourStep({ type: "lemma-tour:step", step: -1 }), -1);
    for (const step of [-2, 5, 0.5, "1", null, NaN]) assert.equal(readTourStep({ type: "lemma-tour:step", step }), null);
    assert.equal(readTourStep({ type: "navigate", step: 1 }), null);
});

test("adding a sample member updates only that teammate and never duplicates them", async () => {
    addPreviewMember("kit", "rohan", "Can read");
    addPreviewMember("kit", "rohan", "Can edit");
    const marketing = await previewSource.getPodDetail("kit", "Kit");
    assert.equal(marketing.members.filter(person => person.id === "rohan").length, 1);
    assert.equal(marketing.members.find(person => person.id === "rohan")?.can, "Can read");
    const personal = await previewSource.getPodDetail("remy", "Remy");
    assert.equal(personal.members.some(person => person.id === "rohan"), false);
});

test("the Acme app and learned guidance resolve to actual preview resources", async () => {
    const tabs = await previewSource.listTabs("kit");
    const profile = await previewSource.getProfile("kit");
    assert.equal(profile.projects[0].tabId, tabs.find(tab => tab.kind === "app")?.id);
    const skills = await previewSource.listLibrary("kit", "files", "/skills");
    for (const skill of skills.items) {
        const file = await previewSource.readFile("kit", skill.path + "/SKILL.md");
        assert.match(file.text ?? "", /description:/);
        assert.match(file.text ?? "", /name: brand-voice/);
    }
});


test("each sample teammate owns their conversation, app, profile and learned context", async () => {
    const pods = await previewSource.listPods("acme");
    assert.deepEqual(pods.map(pod => pod.name), ["Kit", "Remy", "June", "Scout"]);
    const urls = new Set<string>();
    const conversations = new Set<string>();
    for (const pod of pods) {
        const tabs = await previewSource.listTabs(pod.id);
        const app = tabs.find(tab => tab.kind === "app")!;
        urls.add(app.url!);
        assert.match(app.url!, new RegExp("teammate=" + pod.id));
        const profile = await previewSource.getProfile(pod.id);
        assert.equal(profile.name, pod.name);
        assert.equal(profile.projects[0].name, app.label);
        const conversation = await previewSource.getConversation(pod.id);
        conversations.add(conversation.messages[1].text!);
        const detail = await previewSource.getPodDetail(pod.id, pod.name);
        assert.equal(detail.teammate.name, pod.name);
        const file = await previewSource.readFile(pod.id, "/skills/brand-voice/SKILL.md");
        assert.match(file.text!, new RegExp(pod.name));
    }
    assert.equal(urls.size, 4);
    assert.equal(conversations.size, 4);
    assert.deepEqual(await previewSource.listPods("another-org"), []);
});

test("the preview opens on conversation and only the app tour step opens Launch studio", () => {
    assert.equal(previewTabForStep(-1), "conversation");
    assert.equal(previewTabForStep(0), "conversation");
    assert.equal(previewTabForStep(1), "conversation");
    assert.equal(previewTabForStep(2), "profile");
    assert.equal(previewTabForStep(3), "app:launch");
    assert.equal(previewTabForStep(4), "conversation");
});

// The lazy source proxy calls methods without an object receiver.
test("anonymous preview lookup works through a detached source method", async () => {
    const getPod = previewSource.getPod;
    for (const pod of await previewSource.listPods("acme")) {
        assert.deepEqual(await getPod(pod.id), pod);
    }
    assert.equal(await getPod("unknown"), null);
});
