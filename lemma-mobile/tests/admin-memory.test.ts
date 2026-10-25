import { describe, expect, it } from "vitest";
import { isNoSuchFolder, memoryLine, openingScope, readNotes, scopeEmpty, scopeNote } from "../src/areas/admin/memory/model";
import { memorySample } from "../src/areas/admin/memory/sample";
import type { LibraryItem } from "../src/thread/library-item";

/** What a Lemma remembers (PARITY.md, F): its notes, read from the folders
 *  it writes them in. */

describe("reading the notes", () => {
    it("reads Support's sample notes, newest first, with no index among them", async () => {
        const source = memorySample();
        const notes = await readNotes((directory, page) => source.list("sample-support-1140", directory, page));
        expect(notes).toHaveLength(6);
        expect(notes.map((one) => one.path)).not.toContain("/memory/AGENTS.md");
        expect(notes[0].topic).toBe("Shipping delays");
        expect(notes.filter((one) => one.private).map((one) => one.topic)).toEqual(["How ada likes drafts", "Ada on call"]);
        expect(memoryLine(notes).text).toBe("6 notes · 3 this week");
    });

    it("lists each folder once however many walks pass through it", async () => {
        const asked: string[] = [];
        await readNotes(async (directory) => {
            asked.push(directory);
            return { items: [] as LibraryItem[], next: null };
        });
        expect(asked.filter((one) => one === "/")).toHaveLength(1);
    });

    it("counts a folder that went between two steps as holding nothing", async () => {
        const gone = Object.assign(new Error("Directory not found in this pod"), { statusCode: 400 });
        const notes = await readNotes(async (directory) => {
            if (directory === "/") return { items: [{ id: "m", name: "memory", kind: "folder", path: "/memory", updated: "", detail: "" }], next: null };
            throw gone;
        });
        expect(notes).toEqual([]);
        expect(isNoSuchFolder(gone)).toBe(true);
        expect(isNoSuchFolder(Object.assign(new Error("x"), { statusCode: 404 }))).toBe(true);
        expect(isNoSuchFolder(new Error("Pod is busy"))).toBe(false);
        await expect(readNotes(async () => { throw new Error("offline"); })).rejects.toThrow("offline");
    });

    it("says nothing is written down for a Lemma with no notes", async () => {
        const source = memorySample();
        const notes = await readNotes((directory, page) => source.list("sample-books-986", directory, page));
        expect(memoryLine(notes).text).toBe("Nothing written down yet");
        expect(memoryLine(null).text).toBeNull();
    });
});

describe("shared and personal", () => {
    const note = (isPrivate: boolean) => ({ path: "/x.md", topic: "X", gloss: "", updated: "", private: isPrivate });
    it("opens on shared unless only personal notes exist", () => {
        expect(openingScope([note(false), note(true)], null)).toBe("shared");
        expect(openingScope([note(true)], null)).toBe("personal");
        expect(openingScope([note(true)], "shared")).toBe("shared");
    });

    it("says who can read each", () => {
        expect(scopeNote("shared", "Support")).toBe("Everyone in Support can read these.");
        expect(scopeNote("personal", "Support")).toBe("Only you can read these. Support keeps them for you.");
        expect(scopeEmpty("personal", "Support")).toMatch(/learns only from you/);
    });
});
