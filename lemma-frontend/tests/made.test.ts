import test from "node:test";
import assert from "node:assert/strict";
import { madeIn, rememberMade } from "../src/data/made.ts";
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
