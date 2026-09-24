import test from "node:test";
import assert from "node:assert/strict";
import { lemma } from "../src/session/client.ts";
import { liveSource } from "../src/data/live.ts";

test("a teammate with no apps still offers the Apps view", async context => {
    context.mock.method(lemma("empty-apps").apps, "list", async () => ({ items: [] }));
    const tabs = await liveSource.listTabs("empty-apps");
    assert.ok(tabs.some(tab => tab.id === "apps" && tab.label === "Apps"));
    assert.equal(tabs.filter(tab => tab.kind === "app").length, 0);
});

test("app discovery stays reachable when the app list is unavailable", async context => {
    context.mock.method(lemma("unavailable-apps").apps, "list", async () => { throw new Error("offline"); });
    const tabs = await liveSource.listTabs("unavailable-apps");
    assert.ok(tabs.some(tab => tab.id === "apps"));
    assert.ok(tabs.some(tab => tab.id === "conversation"));
});

import { APP_CATEGORIES } from "../src/stage/app-ideas.ts";

test("each app category offers four distinct ideas with specific build requests", () => {
    assert.deepEqual(APP_CATEGORIES.map(category => category.name), ["Personal", "Marketing", "Product", "Sales", "Engineering"]);
    for (const category of APP_CATEGORIES) {
        assert.equal(category.ideas.length, 4, category.name);
        assert.equal(new Set(category.ideas.map(idea => idea.name)).size, 4);
        assert.equal(new Set(category.ideas.map(idea => idea.prompt)).size, 4);
        for (const idea of category.ideas) assert.ok(idea.prompt.includes("Let's build "));
    }
});
