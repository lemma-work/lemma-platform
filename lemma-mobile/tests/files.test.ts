import { describe, expect, it } from "vitest";
import {
    canMoveInto, crumbs, detailOf, expiresIn, glyphOf, hitsOf, isInternal, isPersonal, itemOf, kindOf, makePageIn, ordered,
    pageFolderFor, readUrlOf, sharedLinkOf, wakeLine, wakeRunOf,
} from "../src/areas/files/model";
import type { LibraryItem } from "../src/areas/files/source";

const item = (path: string, extra: Partial<LibraryItem> = {}): LibraryItem => ({
    id: path, name: path.split("/").pop() ?? path, kind: "file", path, updated: "2026-10-20T10:00:00Z", detail: "", ...extra,
});

describe("what kind a file is", () => {
    it("reads the name when the type says nothing", () => {
        expect(kindOf("application/octet-stream", "/clips/launch.mp4")).toBe("video");
        expect(kindOf("", "/Notes/Brief.pdf")).toBe("pdf");
        expect(kindOf("", "/Notes/Mark.png")).toBe("image");
        expect(kindOf("", "/a/voice.m4a")).toBe("audio");
    });

    it("tells a page from text and from a web page", () => {
        expect(kindOf("text/markdown", "/pages/Plan.md")).toBe("markdown");
        expect(kindOf("text/csv", "/Notes/Numbers.csv")).toBe("text");
        expect(kindOf("application/json", "/Notes/Settings.json")).toBe("text");
        expect(kindOf("text/html", "/Notes/Prices.html")).toBe("html");
    });

    it("leaves an office file to another app", () => {
        expect(kindOf("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "/Notes/Contract.docx")).toBe("binary");
    });
});

describe("a listing", () => {
    it("puts folders first, then what changed last", () => {
        const rows = ordered([
            item("/a.md", { updated: "2026-10-01T00:00:00Z" }),
            item("/Notes", { kind: "folder", updated: "2026-09-01T00:00:00Z" }),
            item("/b.md", { updated: "2026-10-05T00:00:00Z" }),
        ]);
        expect(rows.map((one) => one.path)).toEqual(["/Notes", "/b.md", "/a.md"]);
    });

    it("leaves out the platform's own folders", () => {
        expect(isInternal(item("/.lemma/cover.png"))).toBe(true);
        expect(isInternal(item("/app/node_modules/x.js"))).toBe(true);
        expect(isInternal(item("/Notes/Welcome.md"))).toBe(false);
    });

    it("reads a row from the server's file summary", () => {
        const row = itemOf({ id: "f1", name: "Brief.pdf", path: "/Notes/Brief.pdf", kind: "FILE", mime_type: "application/pdf", size_bytes: 2048, status: "FAILED", updated_at: "2026-10-20T10:00:00Z" });
        expect(row).toMatchObject({ kind: "file", mime: "application/pdf", size: 2048, status: "FAILED" });
        expect(itemOf({ id: "d1", name: "Notes", path: "/Notes", kind: "FOLDER" }).kind).toBe("folder");
    });

    it("says what a file is in a word, with its size", () => {
        expect(detailOf(item("/Notes/Brief.pdf", { mime: "application/pdf", size: 2048 }))).toBe("PDF · 2 KB");
        expect(detailOf(item("/pages/Plan.md"))).toBe("Page");
        expect(detailOf(item("/Notes", { kind: "folder" }))).toBe("Folder");
        expect(glyphOf(item("/Notes/Prices.html"))).toBe("web");
        expect(glyphOf(item("/Notes/Numbers.csv"))).toBe("data");
    });
});

describe("places", () => {
    it("knows your own files", () => {
        expect(isPersonal("/me")).toBe(true);
        expect(isPersonal("/me/Draft.md")).toBe(true);
        expect(isPersonal("/meetings/notes.md")).toBe(false);
    });

    it("draws the trail from a location's root", () => {
        expect(crumbs("/Notes/2026", "/")).toEqual([{ name: "Notes", path: "/Notes" }, { name: "2026", path: "/Notes/2026" }]);
        expect(crumbs("/me/Receipts", "/me")).toEqual([{ name: "Receipts", path: "/me/Receipts" }]);
    });

    it("never moves a folder into itself, or somewhere it already is", () => {
        expect(canMoveInto(item("/Notes", { kind: "folder" }), "/Notes/2026")).toBe(false);
        expect(canMoveInto(item("/Notes", { kind: "folder" }), "/Archive")).toBe(true);
        expect(canMoveInto(item("/Notes/a.md"), "/Notes")).toBe(false);
        expect(canMoveInto(item("/Notes/a.md"), "/")).toBe(true);
    });

    it("puts a new page in Pages from the top, and where you are anywhere else", () => {
        expect(pageFolderFor("/")).toBe("/pages");
        expect(pageFolderFor("/Notes")).toBe("/Notes");
        expect(pageFolderFor("/me")).toBe("/me");
    });
});

describe("making a page", () => {
    it("takes the next free name when the server says one is taken", async () => {
        const made: string[] = [];
        const create = async (path: string) => {
            if (path === "/Notes/Weekly update.md") throw Object.assign(new Error("taken"), { statusCode: 409 });
            made.push(path);
        };
        const path = await makePageIn(create, "/Notes", "Weekly update", "# Weekly update\n", new Set());
        expect(path).toBe("/Notes/Weekly update 2.md");
        expect(made).toEqual(["/Notes/Weekly update 2.md"]);
    });

    it("skips names the listing already shows", async () => {
        const path = await makePageIn(async () => undefined, "/pages", "Untitled", "# Untitled\n", new Set(["untitled.md", "untitled 2.md"]));
        expect(path).toBe("/pages/Untitled 3.md");
    });

    it("stops on any other failure", async () => {
        await expect(makePageIn(async () => { throw Object.assign(new Error("no"), { statusCode: 403 }); }, "/pages", "Untitled", "", new Set())).rejects.toThrow("no");
    });
});

describe("search", () => {
    it("keeps one row per file, its best passage first", () => {
        const hits = hitsOf([
            { path: "/a.md", content: "low", score: 0.2 },
            { path: "/b.md", content: "## Refunds   over $200", score: 0.9 },
            { path: "/a.md", content: "high", score: 0.7 },
        ]);
        expect(hits.map((one) => one.path)).toEqual(["/b.md", "/a.md"]);
        expect(hits[0].snippet).toBe("Refunds over $200");
        expect(hits[1].snippet).toBe("high");
    });
});

describe("share links", () => {
    it("takes the code from the link the server made", () => {
        const link = sharedLinkOf("https://lemma.work/", { signed_url: "https://api.lemma.work/public/s/abc123", expires_at: "2026-10-24T12:00:00Z", max_hits: 50 });
        expect(link).toEqual({ code: "abc123", readUrl: "https://lemma.work/d/abc123", expiresAt: "2026-10-24T12:00:00Z", maxHits: 50 });
        expect(readUrlOf("http://localhost:3761", "x")).toBe("http://localhost:3761/d/x");
    });

    it("says how long a link has left", () => {
        const now = Date.parse("2026-10-24T09:00:00Z");
        expect(expiresIn("2026-10-24T09:30:00Z", now)).toBe("Expires in under an hour");
        expect(expiresIn("2026-10-24T12:00:00Z", now)).toBe("Expires in 3 hours");
        expect(expiresIn("2026-10-27T09:00:00Z", now)).toBe("Expires in 3 days");
        expect(expiresIn("2026-10-24T08:00:00Z", now)).toBe("Expired");
    });
});

describe("an agent asked in a comment", () => {
    it("reads its wake-up's run for the comment", () => {
        const run = wakeRunOf({ status: "dispatched", payload: { id: "c9" }, target_run_id: "conv-1", target_kind: "AGENT" });
        expect(run).toMatchObject({ status: "DISPATCHED", tone: "ok", subjectId: "c9", targetRunId: "conv-1", targetKind: "agent" });
    });

    it("says what it is doing in one line", () => {
        expect(wakeLine("Support", null, 10_000)).toBe("Support will pick this up in a moment.");
        expect(wakeLine("Support", null, 120_000)).toBe("Support hasn’t picked this up.");
        expect(wakeLine("Support", wakeRunOf({ status: "FAILED", error_type: "Timeout" }), 0)).toBe("Support couldn’t answer: Timeout.");
        expect(wakeLine("Support", wakeRunOf({ status: "DISPATCHED" }), 0)).toBe("Support is working on it.");
    });
});
