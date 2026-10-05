import type { Turn } from "./turns";

/** The turns just built, with every one that did not change swapped back for
 *  the object already on screen.
 *
 *  `buildTurns` makes new objects for the whole conversation whenever a message
 *  lands — a tool call, a tool return, a line of the answer — and a turn row
 *  memoised on its turn would then redraw every turn in the conversation for
 *  every one of them. Handing back the previous object for a turn whose content
 *  is the same lets the row skip the redraw, and only the turn that changed —
 *  almost always the last — draws again.
 *
 *  Compared by content, serialised once per turn object and remembered against
 *  it, rather than by a hand-picked fingerprint: a fingerprint that missed a
 *  field (an approval's decision, a card's result arriving) would leave a row
 *  showing the old state, which is a worse bug than the redraw it saves. */

const serialised = new WeakMap<Turn, string>();

function contentOf(turn: Turn): string {
    let held = serialised.get(turn);
    if (held === undefined) {
        try {
            held = JSON.stringify(turn);
        } catch {
            /* Something that will not serialise never compares equal: the row
               redraws, which is the safe side to be wrong on. */
            held = "unserialisable:" + Math.random();
        }
        serialised.set(turn, held);
    }
    return held;
}

/** Returns `previous` itself when nothing changed at all. */
export function keepUnchanged(previous: readonly Turn[], next: Turn[]): Turn[] {
    if (previous.length === 0) return next;
    const before = new Map(previous.map((turn) => [turn.id, turn]));
    let changed = previous.length !== next.length;
    const kept = next.map((turn, index) => {
        const was = before.get(turn.id);
        if (was && contentOf(was) === contentOf(turn)) {
            if (previous[index] !== was) changed = true;
            return was;
        }
        changed = true;
        return turn;
    });
    return changed ? kept : (previous as Turn[]);
}
