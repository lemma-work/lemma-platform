import test from "node:test";
import assert from "node:assert/strict";
import { fixtureSource, hiredHere } from "../src/data/fixtures.ts";

test("a teammate hired in the sample starts with an empty space", async () => {
    const pod = await fixtureSource.createPod("acme", "Hazel", "Keep the hiring pipeline moving");
    assert.equal(hiredHere(pod.id), true);
    assert.deepEqual((await fixtureSource.listLibrary(pod.id, "files", "/")).items, []);
    assert.deepEqual((await fixtureSource.listLibrary(pod.id, "tables", "/")).items, []);
    assert.deepEqual(await fixtureSource.listConversations(pod.id), []);
    assert.deepEqual(await fixtureSource.listSchedules(pod.id), []);
    assert.ok((await fixtureSource.listTabs(pod.id)).every((tab) => tab.kind !== "app"));
});

test("the sample's own teammates keep their example work", async () => {
    assert.equal(hiredHere("marketing"), false);
    assert.ok((await fixtureSource.listLibrary("marketing", "files", "/")).items.length > 0);
    assert.ok((await fixtureSource.listConversations("marketing")).length > 0);
});
