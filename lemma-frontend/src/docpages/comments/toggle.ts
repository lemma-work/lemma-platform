"use client";

import { useSyncExternalStore } from "react";

/** Whether a page's comments are open, and how many threads are open on it.
 *
 *  Two places need it: the page, which owns the panel, and the top bar,
 *  where the Comment button sits beside Share. A tiny store keyed by path
 *  rather than state lifted into the shell, which has no business holding a
 *  page's comments. */
type Entry = { open: boolean; count: number };

const entries = new Map<string, Entry>();
const listeners = new Set<() => void>();
const EMPTY: Entry = { open: false, count: 0 };

function emit() {
    for (const listen of listeners) listen();
}

export function setComments(path: string, patch: Partial<Entry>) {
    const was = entries.get(path) ?? EMPTY;
    const next = { ...was, ...patch };
    if (next.open === was.open && next.count === was.count) return;
    entries.set(path, next);
    emit();
}

export function toggleComments(path: string) {
    setComments(path, { open: !(entries.get(path) ?? EMPTY).open });
}

export function useCommentsEntry(path: string | null): Entry {
    return useSyncExternalStore(
        (listen) => { listeners.add(listen); return () => { listeners.delete(listen); }; },
        () => (path ? entries.get(path) ?? EMPTY : EMPTY),
        () => EMPTY,
    );
}
