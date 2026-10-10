import test from "node:test";
import assert from "node:assert/strict";
import { forgetMade, madeIn, rememberMade, renamedMade } from "../src/data/made.ts";
import type { LibraryItem } from "../src/data/types.ts";

function folder(name: string, path: string): LibraryItem {
    return { id: path, name, kind: "folder", path, updated: "2026-10-09T09:00:00Z", detail: "Folder" };
}

test("what was made in one pod is not a row of another", () => {
    rememberMade("pod-a", "/", folder("Reports", "/Reports"));

    assert.deepEqual(madeIn("pod-a", "/").map((one) => one.path), ["/Reports"]);
    /* Two sample pods can both hold a folder called Reports. */
    assert.deepEqual(madeIn("pod-b", "/"), []);
});

test("a made item renamed answers to its new name, once", () => {
    rememberMade("pod-c", "/", folder("New", "/New"));

    renamedMade("pod-c", "/New", "Reports", "/Reports");

    assert.deepEqual(madeIn("pod-c", "/").map((one) => [one.name, one.path]), [["Reports", "/Reports"]]);
});

test("a made item renamed keeps its place in its folder", () => {
    rememberMade("pod-d", "/documents", folder("New", "/documents/New"));

    renamedMade("pod-d", "/documents/New", "Reports", "/documents/Reports");

    assert.deepEqual(madeIn("pod-d", "/documents").map((one) => one.path), ["/documents/Reports"]);
    assert.deepEqual(madeIn("pod-d", "/"), []);
});

test("a made item deleted does not come back on the next listing", () => {
    rememberMade("pod-e", "/", folder("New", "/New"));

    forgetMade("pod-e", "/New");

    assert.deepEqual(madeIn("pod-e", "/"), []);
});

test("renaming one pod's item leaves another pod's alone", () => {
    rememberMade("pod-f", "/", folder("New", "/New"));
    rememberMade("pod-g", "/", folder("New", "/New"));

    renamedMade("pod-f", "/New", "Reports", "/Reports");

    assert.deepEqual(madeIn("pod-g", "/").map((one) => one.name), ["New"]);
});
