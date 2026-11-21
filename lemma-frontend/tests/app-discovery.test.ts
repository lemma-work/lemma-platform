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

test("a Lemma whose apps you may not list still offers the Apps view, with no apps", async context => {
    context.mock.method(lemma("forbidden-apps").apps, "list", async () => { throw Object.assign(new Error("forbidden"), { statusCode: 403 }); });
    const tabs = await liveSource.listTabs("forbidden-apps");
    assert.ok(tabs.some(tab => tab.id === "apps"));
    assert.ok(tabs.some(tab => tab.id === "conversation"));
    assert.equal(tabs.filter(tab => tab.kind === "app").length, 0);
});

test("an app list that failed is a failure, never an empty list", async context => {
    /* "No apps yet" over a failed read would send somebody away from apps
       that are there (design.md, "States"). */
    context.mock.method(lemma("unavailable-apps").apps, "list", async () => { throw new Error("offline"); });
    await assert.rejects(liveSource.listTabs("unavailable-apps"), /offline/);
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
