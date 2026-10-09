import type { LibraryItem } from "./types";

/** What was made in this session, by the folder it was made in.
 *
 *  The sample source is a shape to judge rather than a place to keep things,
 *  but a button that leaves nothing behind is a shape that lies about the
 *  product. What is made here shows up in its folder, and a reload puts the
 *  sample back as it was — the same promise hiring a teammate makes.
 */
const MADE = new Map<string, LibraryItem[]>();

export function rememberMade(directory: string, item: LibraryItem): void {
    const here = MADE.get(directory) ?? [];
    MADE.set(directory, [item, ...here.filter((one) => one.path !== item.path)]);
}

/** What was made in `directory`, newest first. */
export function madeIn(directory: string): LibraryItem[] {
    return MADE.get(directory) ?? [];
}
