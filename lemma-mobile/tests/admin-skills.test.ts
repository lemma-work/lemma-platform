import { describe, expect, it } from "vitest";
import { NEW_SKILL_ASK, readSkills, revisedOn, shortCount, skillMeta, skillsLine } from "../src/areas/admin/skills/model";
import { skillsSample } from "../src/areas/admin/skills/sample";
import { sayUnloadable } from "@web/skills/skills";

/** A Lemma's skills (PARITY.md, F): read from `/skills`, one `SKILL.md` a
 *  skill, and what each row says. */

const GOOD = "---\nname: refund-replies\ndescription: Drafts refund replies. Use when a customer asks for money back.\n---\n\n# Steps\n\nFind the order and write the reply.\n";

describe("reading the skills", () => {
    it("makes a skill of each folder directly under /skills, saying why one will not load", async () => {
        const list = async (directory: string) => ({
            items: [
                { name: "refund-replies", kind: "folder", path: `${directory}/refund-replies`, updated: "2026-10-20T10:00:00Z" },
                { name: "renamed", kind: "folder", path: `${directory}/renamed/`, updated: "" },
                { name: "missing", kind: "folder", path: `${directory}/missing`, updated: "" },
                { name: "loose.md", kind: "file", path: `${directory}/loose.md`, updated: "" },
            ],
            next: null,
        });
        const read = async (path: string) => {
            if (path.includes("/refund-replies/")) return GOOD;
            if (path.includes("/renamed/")) return GOOD;
            return null;
        };
        const cards = await readSkills(list, read);
        expect(cards.map((one) => one.folder)).toEqual(["missing", "refund-replies", "renamed"]);
        expect(cards[1]).toMatchObject({ title: "Refund replies", problem: null, read: true, file: "/skills/refund-replies/SKILL.md" });
        expect(cards[1].words).toBeGreaterThan(5);
        expect(cards[2].problem).toMatch(/folder is `renamed`/);
        expect(cards[0]).toMatchObject({ read: false, title: "Missing" });
        expect(cards[0].problem).toMatch(/could not be read/);
        expect(sayUnloadable(cards)).toBe("2 of these cannot be loaded, so they cannot be used.");
    });

    it("reads every page of the listing", async () => {
        const pages: Record<string, { items: { name: string; kind: string; path: string; updated: string }[]; next: string | null }> = {
            "": { items: [{ name: "a", kind: "folder", path: "/skills/a", updated: "" }], next: "2" },
            "2": { items: [{ name: "b", kind: "folder", path: "/skills/b", updated: "" }], next: null },
        };
        const cards = await readSkills(async (_directory, page) => pages[page ?? ""], async () => null);
        expect(cards.map((one) => one.folder)).toEqual(["a", "b"]);
    });

    it("has no skills where there is no /skills, but fails on anything else", async () => {
        const missing = Object.assign(new Error("Directory not found in this pod"), { statusCode: 400 });
        expect(await readSkills(async () => { throw missing; }, async () => null)).toEqual([]);
        await expect(readSkills(async () => { throw new Error("offline"); }, async () => null)).rejects.toThrow("offline");
    });
});

describe("what a row says", () => {
    const now = new Date("2026-10-24T12:00:00Z");
    it("says how long and when it changed", () => {
        expect(revisedOn("2026-09-20T10:00:00Z", now)).toBe("20 Sep");
        expect(revisedOn("2025-03-02T10:00:00Z", now)).toBe("2 Mar 2025");
        expect(revisedOn("", now)).toBe("");
        expect(shortCount(820)).toBe("820");
        expect(shortCount(1200)).toBe("1.2k");
        expect(shortCount(2000)).toBe("2k");
        expect(skillMeta({ words: 820, updated: "2026-09-20T10:00:00Z" }, now)).toBe("820 words · revised 20 Sep");
        expect(skillMeta({ words: 0, updated: "" }, now)).toBe("");
    });

    it("draws the Settings line", () => {
        expect(skillsLine(null).text).toBeNull();
        expect(skillsLine([]).text).toBe("Nothing yet");
    });

    it("asks the Lemma to write a new one with the skill made for it", () => {
        expect(NEW_SKILL_ASK).toContain("lemma-skill-creator");
        expect(NEW_SKILL_ASK).toContain("/skills");
    });
});

describe("the sample", () => {
    it("gives Support four skills, one of which will not load", async () => {
        const source = skillsSample();
        const cards = await readSkills((directory, page) => source.list("sample-support-1140", directory, page), (path) => source.read("sample-support-1140", path));
        expect(cards).toHaveLength(4);
        expect(skillsLine(cards).text).toBe("4 skills");
        expect(cards.filter((one) => one.problem).map((one) => one.folder)).toEqual(["escalations"]);
        expect(sayUnloadable(cards)).toBe("One of these cannot be loaded, so it cannot be used.");
    });

    it("gives a Lemma with none nothing yet", async () => {
        const source = skillsSample();
        const cards = await readSkills((directory, page) => source.list("sample-books-986", directory, page), (path) => source.read("sample-books-986", path));
        expect(skillsLine(cards).text).toBe("Nothing yet");
    });
});
