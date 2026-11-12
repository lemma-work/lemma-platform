import { describe, expect, it } from "vitest";
import { boardFor, GROUP_OF, NEW_TILE, standingOf } from "../src/ui/lemmas/board";
import type { LemmaSection, SideRow } from "../src/data/types";

const row = (over: Partial<SideRow> = {}): SideRow => ({ key: "r", kind: "chat", title: "t", said: null, at: null, href: "/", needsYou: false, unread: 0, running: false, platforms: [], ...over });
const section = (over: Partial<LemmaSection> = {}): LemmaSection => ({ lemma: { id: "a", name: "A", iconUrl: null } as LemmaSection["lemma"], rows: [], latest: null, waiting: 0, open: true, owns: null, ...over });

describe("a Lemma's standing", () => {
    it("puts what needs you before behind, behind before running, and a quiet week last", () => {
        expect(standingOf(section({ waiting: 1, owns: { standing: "behind" } as LemmaSection["owns"] }))).toBe("needs");
        expect(standingOf(section({ rows: [row({ needsYou: true })] }))).toBe("needs");
        expect(standingOf(section({ owns: { standing: "behind" } as LemmaSection["owns"], rows: [row({ running: true })] }))).toBe("behind");
        expect(standingOf(section({ rows: [row({ running: true })] }))).toBe("running");
        expect(standingOf(section({ open: false }))).toBe("quiet");
        expect(standingOf(section())).toBe("on");
    });
});

describe("the board's two layouts", () => {
    const items = [
        { id: "q", name: "Quiet one", standing: "quiet" as const },
        { id: "n", name: "Needs", standing: "needs" as const },
        { id: "r", name: "Runs", standing: "running" as const },
        { id: "o", name: "On", standing: "on" as const },
    ];

    it("orders by standing in both, so a face travels the least it can", () => {
        const board = boardFor(items, 360, true);
        expect(board.order).toEqual(["n", "r", "o", "q", NEW_TILE]);
        expect(board.grid.get("n")!.y).toBe(0);
        expect(board.grid.get("o")!.x).toBeGreaterThan(board.grid.get("r")!.x);
    });

    it("heads each group once, running and on track together", () => {
        const board = boardFor(items, 360, false);
        expect(board.headers.map((header) => header.label)).toEqual(["Needs you", "On track", "Quiet"]);
        expect(GROUP_OF.running).toBe(GROUP_OF.on);
        expect(board.list.get("r")!.y).toBeLessThan(board.list.get("o")!.y);
    });

    it("lays four columns on a wide screen and three on a phone", () => {
        expect(boardFor(items, 360, true).grid.get(NEW_TILE)!.y).toBeGreaterThan(0);
        expect(boardFor([...items, { id: "x", name: "X", standing: "on" }], 480, false).gridHeight).toBe(boardFor(items, 480, false).gridHeight + 118);
    });
});
