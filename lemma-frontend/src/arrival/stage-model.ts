/** What stands around a Lemma on the way in, as data.
 *
 *  The left side of every step is one scene: Lemma's bars until your Lemma
 *  is made, then its face, with everything you choose attaching to it. Apps
 *  sit in spots over its head -- empty when it is first made, your picks
 *  while you choose, then going in and in. The places it can be reached
 *  dock below it, and you stand beside it. */

export type SlotState = "empty" | "picked" | "going" | "next" | "in" | "stopped";

export interface StageSlot {
    key: string;
    slug: string | null;
    state: SlotState;
    /** How far it has gone in, 0 to 1. */
    progress: number;
    /** Picked, and on its way over from the list: drawn once it lands. */
    landing?: boolean;
    /** Its name, where the spot can be pressed (a Lemma's own page). */
    label?: string;
}

/** More than this many spots crowd the face; the rest are in the list. */
export const MAX_SLOTS = 4;

/** Where the spots sit around the face, in degrees from the right, clockwise:
 *  an arc over its head, spread for however many there are. */
export function slotAngles(count: number): number[] {
    switch (Math.min(count, MAX_SLOTS)) {
        case 0: return [];
        case 1: return [270];
        case 2: return [222, 318];
        case 3: return [205, 270, 335];
        default: return [196, 244, 296, 344];
    }
}

/** Where each place it can be reached docks, under the face, and where you
 *  stand beside it. */
export const PLACE_ANGLES: Record<string, number> = { EMAIL: 118, WHATSAPP: 62, TELEGRAM: 90, SLACK: 34 };
export const YOU_ANGLE = 150;

/** Just made: two empty spots say apps go here. */
export function emptySlots(): StageSlot[] {
    return [0, 1].map((index) => ({ key: `empty-${index}`, slug: null, state: "empty", progress: 0 }));
}

/** While they choose: their picks, or the empty spots when there are none. */
export function pickedSlots(picks: readonly { slug: string; landing?: boolean }[]): StageSlot[] {
    if (picks.length === 0) return emptySlots();
    return picks.slice(0, MAX_SLOTS).map((pick) => ({ key: pick.slug, slug: pick.slug, state: "picked", progress: 0, landing: pick.landing }));
}

export type RunKind = "waiting" | "adding" | "added" | "failed" | "asks";

/** While they go in, and after: each pick by how far along it is. */
export function installSlots(rows: readonly { slug: string; kind: RunKind; progress: number }[]): StageSlot[] {
    return rows.slice(0, MAX_SLOTS).map((row) => ({
        key: row.slug,
        slug: row.slug,
        state: row.kind === "added" ? "in" : row.kind === "adding" ? "going" : row.kind === "waiting" ? "next" : "stopped",
        progress: row.kind === "added" ? 1 : Math.max(0, Math.min(1, row.progress)),
    }));
}
