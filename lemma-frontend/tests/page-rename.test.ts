import test from "node:test";
import assert from "node:assert/strict";
import { nameForTitle, relink, renamePage, wantsRename, type RenameOps } from "../src/docpages/rename.ts";

test("a title becomes a file name without the characters a path cannot carry", () => {
    assert.equal(nameForTitle("  Launch plan: Q4 / EU  "), "Launch plan Q4 EU");
    assert.equal(nameForTitle("..hidden"), "hidden");
    assert.equal(nameForTitle("###"), "");
});

test("only a real, different title asks for a rename", () => {
    assert.equal(wantsRename("/pages/Untitled 3.md", "Launch plan"), true);
    assert.equal(wantsRename("/pages/Launch plan.md", "Launch plan"), false);
    assert.equal(wantsRename("/pages/Untitled.md", "Untitled"), false);
    assert.equal(wantsRename("/pages/Untitled.md", "   "), false);
});

test("links into the page's folders follow it, encoded or not", () => {
    const text = "![](/pages/Untitled 3-files/a.png) [Sub](/pages/Untitled%203/child.md) [Other](/pages/Untitled 30.md)";
    assert.equal(
        relink(text, "/pages/Untitled 3.md", "/pages/Plan.md"),
        "![](/pages/Plan-files/a.png) [Sub](/pages/Plan/child.md) [Other](/pages/Untitled 30.md)",
    );
});

function fakeDisk(files: Record<string, string>, folders: string[] = []) {
    const disk = new Map(Object.entries(files));
    const dirs = new Set(folders);
    const moved: string[] = [];
    const ops: RenameOps = {
        move: async (from, to) => {
            if (disk.has(to) || dirs.has(to)) throw Object.assign(new Error("taken"), { statusCode: 409 });
            if (dirs.has(from)) { dirs.delete(from); dirs.add(to); moved.push(from + " -> " + to); return; }
            if (!disk.has(from)) throw Object.assign(new Error("gone"), { statusCode: 404 });
            disk.set(to, disk.get(from)!); disk.delete(from); moved.push(from + " -> " + to);
        },
        read: async (path) => disk.get(path) ?? null,
        write: async (path, text) => { disk.set(path, text); },
        moveComments: async (from, to) => { moved.push("comments " + from + " -> " + to); },
    };
    return { disk, dirs, moved, ops };
}

test("a rename takes a free name, moves the page's folders, and keeps its links and comments", async () => {
    const { disk, dirs, moved, ops } = fakeDisk({
        "/pages/Untitled.md": "# Plan\n\n![](/pages/Untitled-files/x.png)",
        "/pages/Plan.md": "# someone else's",
    }, ["/pages/Untitled-files"]);
    const to = await renamePage(ops, "/pages/Untitled.md", "Plan");
    assert.equal(to, "/pages/Plan 2.md");
    assert.equal(disk.get("/pages/Plan 2.md"), "# Plan\n\n![](/pages/Plan 2-files/x.png)");
    assert.equal(disk.get("/pages/Plan.md"), "# someone else's");
    assert.ok(dirs.has("/pages/Plan 2-files"));
    assert.ok(moved.includes("comments /pages/Untitled.md -> /pages/Plan 2.md"));
});

test("a sub-page's parent is relinked to the new name", async () => {
    const { disk, ops } = fakeDisk({
        "/pages/Launch.md": "# Launch\n\n[Untitled](/pages/Launch/Untitled-abc.md)",
        "/pages/Launch/Untitled-abc.md": "# Checklist",
    });
    const to = await renamePage(ops, "/pages/Launch/Untitled-abc.md", "Checklist");
    assert.equal(to, "/pages/Launch/Checklist.md");
    assert.equal(disk.get("/pages/Launch.md"), "# Launch\n\n[Untitled](/pages/Launch/Checklist.md)");
});

test("nothing moves when the title already is the name", async () => {
    const { moved, ops } = fakeDisk({ "/pages/Plan.md": "# Plan" });
    assert.equal(await renamePage(ops, "/pages/Plan.md", "Plan"), "/pages/Plan.md");
    assert.deepEqual(moved, []);
});
