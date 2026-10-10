import test from "node:test";
import assert from "node:assert/strict";
import { fixtureSource } from "../src/data/fixtures.ts";
import { rememberMade } from "../src/data/made.ts";
import type { LibraryItem } from "../src/data/types.ts";

function folder(name: string, path: string): LibraryItem {
    return { id: path, name, kind: "folder", path, updated: "2026-10-09T09:00:00Z", detail: "Folder" };
}

test("a folder made in the sample opens onto what was put in it", async () => {
    const made = folder("New", "/New");
    rememberMade("pod-one", "/", made);

    assert.deepEqual((await fixtureSource.listLibrary("pod-one", "files", "/New")).items, []);
    assert.deepEqual((await fixtureSource.listLibrary("pod-one", "files", "/")).items[0], made);
});

test("the sample's brief stays in the folder it belongs to", async () => {
    const items = (await fixtureSource.listLibrary("marketing", "files", "/documents")).items;

    assert.deepEqual(items.map((one) => one.path), ["/documents/brief.md"]);
});
