import { freeName } from "./templates";
import { pageDirs } from "./page-paths";

/** A page's file name follows its title.
 *
 *  A page is a markdown file, and the file's name is what the Pages list, the
 *  breadcrumb and every link show — so a page titled "Launch plan" living at
 *  `Untitled 3.md` reads as "Untitled 3" everywhere but inside itself. Renaming
 *  moves more than one file, though: a page owns a `<name>-files` folder (its
 *  images and attachments) and a `<name>` folder (its sub-pages), links inside
 *  it point into both, and a sub-page is linked from its parent. All of those
 *  move together or the page breaks. Links from unrelated pages are not
 *  chased; they would need a search of every page. */

/** The file name a title asks for: the title itself, minus what a path
 *  cannot carry. Empty when there is nothing usable left. */
export function nameForTitle(title: string): string {
    return title
        .replace(/[\\/:*?"<>|#\u0000-\u001f]/g, " ")
        .replace(/\s+/g, " ")
        .trim()
        .replace(/^\.+/, "")
        .slice(0, 80)
        .trim();
}

/** The file name without its folder or extension. */
export function stemOf(path: string): string {
    return path.slice(path.lastIndexOf("/") + 1).replace(/\.(md|markdown)$/i, "");
}

/** Whether this title would give the page a different name. */
export function wantsRename(path: string, title: string): boolean {
    const wanted = nameForTitle(title);
    return wanted !== "" && wanted !== "Untitled" && wanted !== stemOf(path);
}

/** The page's own markdown, and its parent's, with every link into the old
 *  folders — and to the page itself — pointed at the new ones. */
export function relink(markdown: string, from: string, to: string): string {
    const before = pageDirs(from);
    const after = pageDirs(to);
    const moves: [string, string][] = [
        [before.assets + "/", after.assets + "/"],
        [before.children + "/", after.children + "/"],
        [from, to],
    ];
    let out = markdown;
    for (const [old, next] of moves) {
        out = out.split(old).join(next);
        /* Links with spaces are written percent-encoded by the editor. */
        const encoded = encodeURI(old);
        if (encoded !== old) out = out.split(encoded).join(encodeURI(next));
    }
    return out;
}

export interface RenameOps {
    /** Move a file or folder; rejects with `statusCode` 409 when the target
     *  exists and 404 when the source does not. */
    move: (from: string, to: string) => Promise<void>;
    read: (path: string) => Promise<string | null>;
    write: (path: string, text: string) => Promise<void>;
    /** Point this page's comments at its new path. Best effort. */
    moveComments: (from: string, to: string) => Promise<void>;
}

const statusOf = (error: unknown) => (error as { statusCode?: number } | null)?.statusCode;

/** Rename a page to follow its title. Returns the new path, or the old one
 *  when there was nothing to do. */
export async function renamePage(ops: RenameOps, from: string, title: string): Promise<string> {
    if (!wantsRename(from, title)) return from;
    const dir = from.slice(0, from.lastIndexOf("/"));
    const seen = new Set<string>();
    let to = "";
    for (let attempt = 0; attempt < 25; attempt++) {
        const candidate = dir + "/" + freeName(nameForTitle(title), seen) + ".md";
        if (candidate === from) return from;
        try {
            await ops.move(from, candidate);
            to = candidate;
            break;
        } catch (error) {
            if (statusOf(error) !== 409) throw error;
            seen.add(candidate.slice(dir.length + 1).toLowerCase());
        }
    }
    if (!to) throw new Error("Couldn’t find a free name for this page.");

    /* Its folders, where it has them. A missing one is the common case. */
    const before = pageDirs(from);
    const after = pageDirs(to);
    for (const [old, next] of [[before.assets, after.assets], [before.children, after.children]]) {
        try {
            await ops.move(old, next);
        } catch (error) {
            if (statusOf(error) !== 404) throw error;
        }
    }

    const own = await ops.read(to);
    if (own !== null) {
        const fixed = relink(own, from, to);
        if (fixed !== own) await ops.write(to, fixed);
    }
    /* A sub-page lives in its parent's `<name>` folder and is linked from the
       parent's file beside it. */
    const parent = dir + ".md";
    if (dir.includes("/")) {
        const text = await ops.read(parent).catch(() => null);
        if (text !== null) {
            const fixed = relink(text, from, to);
            if (fixed !== text) await ops.write(parent, fixed);
        }
    }
    await ops.moveComments(from, to).catch(() => undefined);
    return to;
}
