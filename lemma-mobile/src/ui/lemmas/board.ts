import type { LemmaSection } from "../../data/types";

/** The Lemmas tab is one set of faces in two layouts, a grid and standings
 *  rows, and each face travels between them. This works out where each one
 *  sits in both, so the screen only has to move it. */

export type Standing = "needs" | "behind" | "running" | "on" | "quiet";

/** What a Lemma shows first: something it needs from you outranks being
 *  behind, which outranks running. A Lemma quiet for a week folds away. */
export function standingOf(section: LemmaSection): Standing {
    if (section.waiting > 0 || section.rows.some((row) => row.needsYou)) return "needs";
    if (section.owns?.standing === "behind") return "behind";
    if (section.rows.some((row) => row.running)) return "running";
    if (!section.open) return "quiet";
    return "on";
}

const RANK: Record<Standing, number> = { needs: 0, behind: 1, running: 2, on: 3, quiet: 4 };

/** The standings' groups: running and on track share one. */
export const GROUP_OF: Record<Standing, string> = { needs: "Needs you", behind: "Behind", running: "On track", on: "On track", quiet: "Quiet" };

/** The tile for starting a Lemma, last in both layouts. */
export const NEW_TILE = "__new";

export interface Placed {
    x: number;
    y: number;
    w: number;
    h: number;
    /** The face's centre and size within the board. */
    cx: number;
    cy: number;
    face: number;
}

export interface Board {
    order: string[];
    grid: Map<string, Placed>;
    list: Map<string, Placed>;
    headers: { label: string; y: number }[];
    gridHeight: number;
    listHeight: number;
}

export const GRID = { pad: 12, cellHeight: 118, face: 60, faceTop: 8 } as const;
export const LIST = { header: 32, row: 56, face: 36, faceLeft: 20 } as const;

export function boardFor(items: readonly { id: string; name: string; standing: Standing }[], width: number, withNew: boolean): Board {
    const ordered = [...items].sort((a, b) => RANK[a.standing] - RANK[b.standing] || a.name.localeCompare(b.name));
    const order = [...ordered.map((item) => item.id), ...(withNew ? [NEW_TILE] : [])];
    const columns = width >= 420 ? 4 : 3;
    const cell = (width - GRID.pad * 2) / columns;
    const grid = new Map<string, Placed>();
    order.forEach((id, index) => {
        const x = GRID.pad + (index % columns) * cell;
        const y = Math.floor(index / columns) * GRID.cellHeight;
        grid.set(id, { x, y, w: cell, h: GRID.cellHeight, cx: x + cell / 2, cy: y + GRID.faceTop + GRID.face / 2, face: GRID.face });
    });
    const list = new Map<string, Placed>();
    const headers: { label: string; y: number }[] = [];
    let y = 0;
    let last: string | null = null;
    for (const item of ordered) {
        const group = GROUP_OF[item.standing];
        if (group !== last) {
            headers.push({ label: group, y });
            y += LIST.header;
            last = group;
        }
        list.set(item.id, { x: 0, y, w: width, h: LIST.row, cx: LIST.faceLeft + LIST.face / 2, cy: y + LIST.row / 2, face: LIST.face });
        y += LIST.row;
    }
    if (withNew) {
        y += 8;
        list.set(NEW_TILE, { x: 0, y, w: width, h: LIST.row, cx: LIST.faceLeft + LIST.face / 2, cy: y + LIST.row / 2, face: LIST.face });
        y += LIST.row;
    }
    const rows = Math.ceil(order.length / columns);
    return { order, grid, list, headers, gridHeight: rows * GRID.cellHeight, listHeight: y };
}
