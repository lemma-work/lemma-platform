import type { LibraryItem } from "../../../thread/library-item";
import { changedLately, listIfThere, MEMORY_FOLDERS, notesFrom, type MemoryNote } from "@web/thread/memory-notes";
import type { AboutLine } from "../types";
import { count } from "../words";

/** What a Lemma remembers, read from the files it writes them in (the
 *  web's `useMemoryNotes`, `space/what-it-remembers.tsx`). Pure, given the
 *  listing, so `tests/` can hold it. */

export type ListPage = (directory: string, page?: string) => Promise<{ items: readonly LibraryItem[]; next?: string | null }>;

/** A folder that is not there: the datastore answers a 400 with this
 *  sentence, or a 404. A Lemma that has noted nothing there yet. */
export function isNoSuchFolder(error: unknown): boolean {
    const held = error as { statusCode?: unknown; status?: unknown; message?: unknown } | null;
    if (held?.statusCode === 404 || held?.status === 404) return true;
    return /directory not found/i.test(String(held?.message ?? ""));
}

/** Every note in the Lemma's own note folders, newest first. Each folder is
 *  walked down to from one that exists, and `/` and `/memory` are each
 *  listed once however many walks pass through them. */
export async function readNotes(list: ListPage): Promise<MemoryNote[]> {
    const seen = new Map<string, ReturnType<ListPage>>();
    const once: ListPage = (directory, page) => {
        const key = `${directory}\u0000${page ?? ""}`;
        if (!seen.has(key)) seen.set(key, list(directory, page));
        return seen.get(key)!;
    };
    return notesFrom(await Promise.all(MEMORY_FOLDERS.map(async (folder) => {
        try {
            return { private: folder.private, items: (await listIfThere(once, folder.path)) ?? [] };
        } catch (error) {
            /* A folder that went between two steps is still one the Lemma
               has noted nothing in. */
            if (isNoSuchFolder(error)) return { private: folder.private, items: [] };
            throw error;
        }
    })));
}

/** The Settings tab's line: "12 notes · 3 this week", or "Nothing written
 *  down yet". */
export function memoryLine(notes: readonly MemoryNote[] | null, now: number = Date.now()): AboutLine {
    if (notes === null) return { text: null };
    if (notes.length === 0) return { text: "Nothing written down yet" };
    const lately = notes.filter((note) => changedLately(note.updated, now)).length;
    return { text: count(notes.length, "note", "notes") + (lately ? ` · ${lately} this week` : "") };
}

export type Scope = "shared" | "personal";

/** Which notes to open on: shared, unless there are none and some personal. */
export function openingScope(notes: readonly MemoryNote[], chosen: Scope | null): Scope {
    if (chosen) return chosen;
    const shared = notes.some((note) => !note.private);
    return !shared && notes.some((note) => note.private) ? "personal" : "shared";
}

/** Who can read a scope's notes, said under the switch. */
export function scopeNote(scope: Scope, lemma: string): string {
    return scope === "shared" ? `Everyone in ${lemma} can read these.` : `Only you can read these. ${lemma} keeps them for you.`;
}

/** What an empty scope says. */
export function scopeEmpty(scope: Scope, lemma: string): string {
    return scope === "shared"
        ? "Nothing shared written down yet."
        : `Nothing about you alone yet. What ${lemma} learns only from you lands here.`;
}
