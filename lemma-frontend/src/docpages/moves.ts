import { useSyncExternalStore } from "react";

/** Where a page went when its title moved it.
 *
 *  A chat holds a file by its path — the card the agent put on screen, the
 *  doc a conversation is about — and a rename leaves every one of those
 *  pointing at a file that is no longer there. This is what lets them follow
 *  it for as long as this tab is open; nothing here survives a reload, which
 *  is the gap a path cannot close on its own. */
const moves = new Map<string, string>();
const listeners = new Set<() => void>();
let version = 0;

export function recordMove(podId: string, from: string, to: string): void {
    if (from === to) return;
    moves.set(podId + "|" + from, to);
    version += 1;
    for (const listener of listeners) listener();
}

/** The path a file has now, following each move made since it was named.
 *  Bounded, so two moves that undo each other cannot loop. */
export function followMoves(podId: string, path: string): string {
    let at = path;
    for (let hops = 0; hops < 20; hops++) {
        const next = moves.get(podId + "|" + at);
        if (!next) break;
        at = next;
    }
    return at;
}

function subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
}

/** `followMoves`, re-read when a page moves — a card already on screen when
 *  its file is renamed reads the new path without waiting to be redrawn. */
export function useCurrentPath(podId: string, path: string): string {
    useSyncExternalStore(subscribe, () => version, () => version);
    return followMoves(podId, path);
}
