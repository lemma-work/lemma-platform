import type { LibraryItem } from "./types";

/** What was made in this session, by the pod and the folder it was made in.
 *
 *  The sample source is a shape to judge rather than a place to keep things,
 *  but a button that leaves nothing behind is a shape that lies about the
 *  product. What is made here shows up in its folder, and a reload puts the
 *  sample back as it was — the same promise hiring a teammate makes.
 *
 *  The pod is part of the key because two sample pods can both hold a folder
 *  with the same name: without it, a folder made in one is a row of the other.
 */
const MADE = new Map<string, LibraryItem[]>();

/** A pod id is a UUID and a path never holds a NUL, so the join is unambiguous
 *  however the two are spelled. */
const SEPARATOR = "\u0000";

function keyOf(podId: string, directory: string): string {
    return podId + SEPARATOR + directory;
}

/** The keys this pod has made something under. */
function keysOf(podId: string): string[] {
    const prefix = podId + SEPARATOR;
    return [...MADE.keys()].filter((key) => key.startsWith(prefix));
}

/** The directory an item at `path` sits in. */
function parentOf(path: string): string {
    return path.slice(0, path.lastIndexOf("/")) || "/";
}

export function rememberMade(podId: string, directory: string, item: LibraryItem): void {
    const key = keyOf(podId, directory);
    const here = MADE.get(key) ?? [];
    MADE.set(key, [item, ...here.filter((one) => one.path !== item.path)]);
}

/** What was made in `directory`, newest first. */
export function madeIn(podId: string, directory: string): LibraryItem[] {
    return MADE.get(keyOf(podId, directory)) ?? [];
}

/** Something made here was renamed. It answers to its new name and path from
 *  now on — left alone, the next listing would put the old one back. */
export function renamedMade(podId: string, path: string, name: string, next: string): void {
    for (const key of keysOf(podId)) {
        const here = MADE.get(key) ?? [];
        const found = here.find((one) => one.path === path);
        if (!found) continue;
        MADE.set(key, here.filter((one) => one.path !== path));
        rememberMade(podId, parentOf(next), { ...found, name, path: next });
        return;
    }
}

/** Something made here was deleted. The next listing does not put it back. */
export function forgetMade(podId: string, path: string): void {
    for (const key of keysOf(podId)) {
        const here = MADE.get(key) ?? [];
        if (here.some((one) => one.path === path)) MADE.set(key, here.filter((one) => one.path !== path));
    }
}
