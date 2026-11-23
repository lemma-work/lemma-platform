import type { PickedFile } from "../data/io";

/** Files picked in one screen's box and sent from another's: the new-chat
 *  screen's ask box hands its files to the chat it opens, which uploads
 *  them once the conversation exists (they go into its folder, which does
 *  not exist until then). A route carries words, not files, so the files
 *  wait here under a name the route does carry, and are taken once. */

const held = new Map<string, PickedFile[]>();
let counter = 0;

export function holdFiles(files: readonly PickedFile[]): string | null {
    if (files.length === 0) return null;
    counter += 1;
    const name = "files-" + counter + "-" + Date.now().toString(36);
    held.set(name, [...files]);
    return name;
}

export function takeFiles(name: string | null | undefined): PickedFile[] {
    if (!name) return [];
    const files = held.get(name) ?? [];
    held.delete(name);
    return files;
}
