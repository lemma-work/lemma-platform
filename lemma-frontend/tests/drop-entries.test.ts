import test from "node:test";
import assert from "node:assert/strict";
import { readDropped } from "../src/library/drop-entries.ts";

/** The parts of a drag entry this reads. A directory reader answers a page at
 *  a time — one item here — and an empty page is the end of the list, not an
 *  empty folder, which is the case the walk exists to get right. */
function file(name: string, body = "contents"): FileSystemEntry {
    const made = new File([body], name);
    return {
        isFile: true,
        isDirectory: false,
        name,
        file: (done: (file: File) => void) => done(made),
    } as unknown as FileSystemEntry;
}

function folder(name: string, children: FileSystemEntry[]): FileSystemEntry {
    let at = 0;
    return {
        isFile: false,
        isDirectory: true,
        name,
        createReader: () => ({
            readEntries: (done: (entries: FileSystemEntry[]) => void) => done(children.slice(at, ++at)),
        }),
    } as unknown as FileSystemEntry;
}

test("a dropped file lands in the folder it was dropped on", async () => {
    const tree = await readDropped([file("overview.pdf")], "/");
    assert.deepEqual(tree.folders, []);
    assert.deepEqual(tree.files.map((each) => [each.file.name, each.directory]), [["overview.pdf", "/"]]);
});

test("a dropped folder is walked, not rejected", async () => {
    const tree = await readDropped([folder("Reports", [file("q1.pdf"), folder("2025", [file("q4.pdf")])])], "/me");
    assert.deepEqual(tree.files.map((each) => [each.file.name, each.directory]), [
        ["q1.pdf", "/me/Reports"],
        ["q4.pdf", "/me/Reports/2025"],
    ]);
    /* An upload makes the folders above the file it writes, so neither is a
       folder anything has to create by name. */
    assert.deepEqual(tree.folders, [
        { path: "/me/Reports", holdsFile: true },
        { path: "/me/Reports/2025", holdsFile: true },
    ]);
});

test("a folder holding no file is the one nothing else would create", async () => {
    const tree = await readDropped([folder("Archive", [])], "/");
    assert.deepEqual(tree.folders, [{ path: "/Archive", holdsFile: false }]);
    assert.deepEqual(tree.files, []);
});

test("empty folders inside a tree are made parents first", async () => {
    const tree = await readDropped([folder("A", [folder("B", []), folder("C", [])])], "/me");
    assert.deepEqual(tree.folders, [
        { path: "/me/A", holdsFile: false },
        { path: "/me/A/B", holdsFile: false },
        { path: "/me/A/C", holdsFile: false },
    ]);
});

test("a folder holding a file is reported, and the upload is what makes it", async () => {
    const tree = await readDropped([folder("A", [file("one.txt"), folder("Empty", [])])], "/");
    assert.deepEqual(tree.folders, [
        { path: "/A", holdsFile: true },
        { path: "/A/Empty", holdsFile: false },
    ]);
    assert.deepEqual(tree.files.map((each) => each.file.name), ["one.txt"]);
});

test("what is neither a file nor a folder is nothing here", async () => {
    const tree = await readDropped([{ isFile: false, isDirectory: false, name: "words" } as unknown as FileSystemEntry], "/");
    assert.deepEqual(tree, { folders: [], files: [] });
});

test("everything dropped is read, in the order it was dropped", async () => {
    const tree = await readDropped([folder("First", [file("a.txt")]), file("b.txt"), folder("Second", [])], "/");
    assert.deepEqual(tree.files.map((each) => each.file.name), ["a.txt", "b.txt"]);
    assert.deepEqual(tree.folders, [
        { path: "/First", holdsFile: true },
        { path: "/Second", holdsFile: false },
    ]);
});
