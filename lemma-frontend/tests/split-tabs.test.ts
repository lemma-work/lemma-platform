import test from "node:test";
import assert from "node:assert/strict";
import { layoutForTab } from "../src/shell/split-tabs.ts";

test("every resource opens beside the conversation by default", () => {
    for (const tab of ["library", "file:brief.md", "table:tasks", "record:tasks:1", "computer", "history"]) {
        assert.deepEqual(layoutForTab(tab, false), { main: "conversation", right: tab });
    }
});

test("view in full shows only the selected resource", () => {
    assert.deepEqual(layoutForTab("file:brief.md", true), { main: "file:brief.md", right: null });
});

test("returning to the sidebar restores chat beside the same resource", () => {
    assert.deepEqual(layoutForTab("table:tasks", false), { main: "conversation", right: "table:tasks" });
});

test("apps always open full-width, never in the sidebar", () => {
    for (const tab of ["apps", "app:launch"]) for (const origin of ["conversation", "library"]) {
        assert.deepEqual(layoutForTab(tab, false, origin), { main: tab, right: null });
    }
});

test("selecting conversation leaves no duplicate conversation pane", () => {
    for (const expanded of [false, true]) {
        assert.deepEqual(layoutForTab("conversation", expanded), { main: "conversation", right: null });
    }
});

import { clampPaneWidth } from "../src/shell/split-tabs.ts";

test("sidebar width restores valid preferences and bounds both panes", () => {
    assert.equal(clampPaneWidth(61), 61);
    assert.equal(clampPaneWidth(-10), 35);
    assert.equal(clampPaneWidth(120), 65);
});

test("invalid stored sidebar widths fall back to the default", () => {
    for (const value of [null, "61", {}, NaN, Infinity]) assert.equal(clampPaneWidth(value), 52);
});

test("profile always opens full-width without a sidebar", () => {
    for (const expanded of [false, true]) {
        assert.deepEqual(layoutForTab("profile", expanded), { main: "profile", right: null });
    }
});

test("files and tables opened from Library keep Library on the left", () => {
    for (const selected of ["file:brief.md", "table:tasks"]) {
        assert.deepEqual(layoutForTab(selected, false, "library"), { main: "library", right: selected });
    }
});

test("a row opens beside its source table instead of restoring chat", () => {
    assert.deepEqual(layoutForTab("record:tasks:1", false, "table:tasks"),
        { main: "table:tasks", right: "record:tasks:1" });
});

test("expanding and returning preserve the source view", () => {
    assert.deepEqual(layoutForTab("file:brief.md", true, "library"), { main: "file:brief.md", right: null });
    assert.deepEqual(layoutForTab("file:brief.md", false, "library"), { main: "library", right: "file:brief.md" });
    assert.deepEqual(layoutForTab("library", false, "library"), { main: "library", right: null });
});
