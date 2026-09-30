import test from "node:test";
import assert from "node:assert/strict";
import { PAGE_TEMPLATES, freeName, makePage } from "../src/docpages/templates.ts";

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

test("a new page never replaces one: a taken name is refused and the next is tried", async () => {
    const onDisk = new Set(["/pages/Untitled.md", "/pages/Untitled 2.md"]);
    const create = async (path: string) => {
        if (onDisk.has(path)) throw Object.assign(new Error("exists"), { statusCode: 409 });
        onDisk.add(path);
    };
    /* The listing missed both — stale, or it failed. */
    assert.equal(await makePage(create, "Untitled", "# Untitled", new Set()), "/pages/Untitled 3.md");
});

test("any other failure is the caller's to show, not retried", async () => {
    const create = async () => { throw Object.assign(new Error("forbidden"), { statusCode: 403 }); };
    await assert.rejects(makePage(create, "Untitled", "", new Set()), /forbidden/);
});
