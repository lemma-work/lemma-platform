/** A dropped folder, read before anything is written.
 *
 *  A drag carries entries rather than a flat list of files: dropping a folder
 *  hands over the folder itself, and what is inside it has to be walked. A
 *  directory reader answers one page at a time and an empty page is the end,
 *  so the whole tree is read first and written second — which is also what
 *  keeps a name clash half-way through from leaving the rest unread.
 *
 *  Every folder is reported, with whether a file anywhere under it will make
 *  it on the way in. Writing a file makes the folders above it (`mkdir -p`),
 *  so a folder holding one needs no call of its own; a folder holding none is
 *  the one nothing else creates, and the one that would otherwise be dropped
 *  on the floor.
 */

export interface DroppedTree {
    /** Every folder in the drop, parents before the folders inside them. */
    folders: { path: string; holdsFile: boolean }[];
    /** Files to write, each with the folder it belongs in. */
    files: { file: File; directory: string }[];
}

/** A child of a folder, at the path it was dropped under. */
function pathIn(directory: string, name: string): string {
    const base = directory.replace(/\/+$/, "");
    return (base === "" ? "" : base) + "/" + name;
}

function depth(path: string): number {
    return path.split("/").length;
}

/** Everything a folder holds. `readEntries` hands back one page, and a page
 *  that is empty is the last one — the reader is not a one-shot list. */
async function childrenOf(directory: FileSystemDirectoryEntry): Promise<FileSystemEntry[]> {
    const reader = directory.createReader();
    const all: FileSystemEntry[] = [];
    for (;;) {
        const page = await new Promise<FileSystemEntry[]>((done, failed) => reader.readEntries(done, failed));
        if (page.length === 0) return all;
        all.push(...page);
    }
}

/** What was dropped, as folders to make and files to write. Entries that are
 *  neither (a dragged selection of text, say) are nothing here. */
export async function readDropped(entries: readonly FileSystemEntry[], into: string): Promise<DroppedTree> {
    const files: DroppedTree["files"] = [];
    const folders: DroppedTree["folders"] = [];

    async function walk(entry: FileSystemEntry, directory: string): Promise<void> {
        if (entry.isFile) {
            const file = await new Promise<File>((done, failed) => (entry as FileSystemFileEntry).file(done, failed));
            files.push({ file, directory });
            return;
        }
        if (!entry.isDirectory) return;
        const path = pathIn(directory, entry.name);
        const inside = await childrenOf(entry as FileSystemDirectoryEntry);
        const before = files.length;
        for (const child of inside) await walk(child, path);
        folders.push({ path, holdsFile: files.length > before });
    }

    for (const entry of entries) await walk(entry, into);
    /* Parents first: making a folder whose parent is not there yet fails, and
       making the parent first is what a person watching expects to see. */
    return { folders: folders.sort((a, b) => depth(a.path) - depth(b.path)), files };
}
