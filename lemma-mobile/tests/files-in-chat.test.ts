import { describe, expect, it } from "vitest";
import { barsOf, clockOfSeconds, documentLine, ofCount, photoTiles, readSheet, rowCount, separatorOf, sheetFromMarkdown } from "../src/ui/files-in-chat/model";

/** The file pieces any chat draws: a Lemma's chat and a group alike. */

describe("photos", () => {
    it("one large and two small, +N on the last when there are more", () => {
        expect(photoTiles(1)).toEqual({ shown: 1, more: 0 });
        expect(photoTiles(3)).toEqual({ shown: 3, more: 0 });
        expect(photoTiles(5)).toEqual({ shown: 3, more: 2 });
        expect(photoTiles(0)).toEqual({ shown: 0, more: 0 });
        expect(ofCount(0, 5)).toBe("1 of 5");
    });
});

describe("a spreadsheet's first rows", () => {
    it("reads quotes, doubled quotes, breaks inside quotes and CRLF, and counts every row", () => {
        const csv = "name,note,amount\r\n\"Northwind, Ltd\",\"said \"\"broken\"\"\",240\r\nGlobex,\"two\nlines\",80\r\nInitech,,1180\r\n";
        const sheet = readSheet(csv, { keep: 2 });
        expect(sheet.header).toEqual(["name", "note", "amount"]);
        expect(sheet.rows).toEqual([["Northwind, Ltd", "said \"broken\"", "240"], ["Globex", "two\nlines", "80"]]);
        expect(sheet.total).toBe(3);
    });

    it("finds the separator, skips blank lines, and copes with no last break", () => {
        expect(separatorOf("a;b;c\n1;2;3")).toBe(";");
        expect(separatorOf("a,b", "x.tsv")).toBe("\t");
        const sheet = readSheet("week\tanswered\n41\t31\n\n42\t38", { name: "numbers.tsv" });
        expect(sheet.rows).toEqual([["41", "31"], ["42", "38"]]);
        expect(sheet.total).toBe(2);
        expect(readSheet("")).toEqual({ header: [], rows: [], total: 0 });
    });

    it("takes a converted spreadsheet's first table", () => {
        const markdown = "# Sheet1\n\n| week | answered |\n| --- | --- |\n| 41 | 31 |\n| 42 | 38 |\n| 43 | 40 |\n\nAfter.";
        expect(sheetFromMarkdown(markdown, 2)).toEqual({ header: ["week", "answered"], rows: [["41", "31"], ["42", "38"]], total: 3 });
        expect(sheetFromMarkdown("No table here.")).toBeNull();
        expect(rowCount(1204)).toBe("1,204 rows");
        expect(rowCount(1)).toBe("1 row");
    });
});

describe("a recording", () => {
    it("has the same bars every time, inside their bounds", () => {
        const bars = barsOf("/inbox/voice-note.m4a");
        expect(bars).toEqual(barsOf("/inbox/voice-note.m4a"));
        expect(bars).not.toEqual(barsOf("/inbox/other.m4a"));
        expect(bars.length).toBe(36);
        expect(Math.min(...bars)).toBeGreaterThanOrEqual(0.18);
        expect(Math.max(...bars)).toBeLessThanOrEqual(1);
        expect(clockOfSeconds(42.9)).toBe("0:42");
        expect(clockOfSeconds(725)).toBe("12:05");
    });
});

describe("a document's line", () => {
    it("says kind, pages and size, leaving out what is not known", () => {
        expect(documentLine("Brief.pdf", 4, "120 KB")).toBe("PDF · 4 pages · 120 KB");
        expect(documentLine("Brief.pdf", 1, "")).toBe("PDF · 1 page");
        expect(documentLine("notes", null, "2 KB")).toBe("File · 2 KB");
    });
});
