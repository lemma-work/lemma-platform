import test from "node:test";
import assert from "node:assert/strict";
import { PAGE_TEMPLATES, freeName } from "../src/docpages/templates.ts";

test("a template's page takes the next free name in the folder", () => {
    assert.equal(freeName("Weekly update", new Set()), "Weekly update");
    assert.equal(freeName("Weekly update", new Set(["weekly update.md"])), "Weekly update 2");
    assert.equal(freeName("Weekly update", new Set(["weekly update.md", "weekly update 2.md"])), "Weekly update 3");
});

test("every template is a titled page, and the guide names the space's bot", () => {
    for (const template of PAGE_TEMPLATES) {
        const body = template.body("Kit", new Date("2026-09-30T10:00:00Z"));
        assert.match(body, /^# /, template.id + " starts with its title");
    }
    const guide = PAGE_TEMPLATES.find((one) => one.id === "guide")!.body("Kit", new Date());
    assert.match(guide, /Ask Kit to write/);
    assert.match(guide, /```lemma-widget\n<!doctype html>/);
    assert.doesNotMatch(guide, /ChatGPT/);
});
