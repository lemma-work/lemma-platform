import * as FileSystem from "expo-file-system/legacy";
import { onSession } from "../session/auth";

/** What the chat keeps on this phone between launches that is not a secret:
 *  unsent drafts, and the reply choice made in a chat that lives on a chat
 *  platform too. The web keeps both in `localStorage`; a phone has no
 *  synchronous store, so this is one, held in memory and written to a file
 *  in the app's own documents a moment after it changes.
 *
 *  Read once at launch. Until that read lands the store answers as empty,
 *  and `whenKept` says when it has: the composer fills its draft then, if
 *  nobody has typed in the meantime.
 *
 *  A draft is somebody's unsent words, so signing out drops all of it, as
 *  the web drops its drafts when the person at the browser changes. */

export interface KeyValueStore {
    getItem(key: string): string | null;
    setItem(key: string, value: string): void;
    removeItem(key: string): void;
}

const FILE = `${FileSystem.documentDirectory ?? ""}lemma-chat-kept.json`;
const WRITE_MS = 400;

const held = new Map<string, string>();
let loaded = false;
let timer: ReturnType<typeof setTimeout> | null = null;

const loading: Promise<void> = (async () => {
    try {
        const info = await FileSystem.getInfoAsync(FILE);
        if (info.exists) {
            const parsed: unknown = JSON.parse(await FileSystem.readAsStringAsync(FILE));
            if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
                for (const [name, value] of Object.entries(parsed)) {
                    /* Written before the read landed wins: it is newer. */
                    if (typeof value === "string" && !held.has(name)) held.set(name, value);
                }
            }
        }
    } catch {
        /* Unreadable or absent: start empty. */
    } finally {
        loaded = true;
    }
})();

function flush(): void {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
        timer = null;
        const body = JSON.stringify(Object.fromEntries(held));
        void FileSystem.writeAsStringAsync(FILE, body).catch(() => undefined);
    }, WRITE_MS);
}

export const kept: KeyValueStore = {
    getItem: (key) => held.get(key) ?? null,
    setItem: (key, value) => {
        held.set(key, value);
        flush();
    },
    removeItem: (key) => {
        if (!held.delete(key)) return;
        flush();
    },
};

/** Whether the launch's read has landed. */
export function isKept(): boolean {
    return loaded;
}

/** Resolves once the launch's read has landed. */
export function whenKept(): Promise<void> {
    return loading;
}

onSession((event) => {
    if (event !== "signed-out") return;
    held.clear();
    flush();
});
