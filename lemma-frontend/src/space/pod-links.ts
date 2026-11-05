import type { AskablePod, LibraryItem, LinkShare, PodLink, ResourcePage } from "@/data";

/** What the Connect dialog decides, apart from drawing it: which teammates may
 *  be offered as the one asking, what is already shared with each, and which
 *  of this teammate's tables and folders may be shared at all. */

export function sameShare(a: LinkShare, b: LinkShare): boolean {
    return a.kind === b.kind && a.name === b.name;
}

/** "Reads invoices and /reports", or what it reads when nothing was shared. */
export function sharedLine(shared: LinkShare[]): string {
    if (shared.length === 0) return "Reads only what is Public here";
    return "Reads " + shared.map((one) => one.name).join(", ");
}

/** One the dialog can offer as the asking teammate. */
export interface AskerChoice {
    id: string;
    name: string;
    iconUrl: string | null;
    /** Already allowed to ask: choosing it edits what it reads. */
    connected: boolean;
}

/** The teammates the person may connect: only ones they are in themselves.
 *
 *  The server refuses a connection from a teammate the person is not in, and an
 *  organization's owner sees every teammate in `listPods` -- so offering those
 *  only led to a refusal. `throughYou` on this teammate's own askable list is
 *  exactly "you are in both". Ones already connected are marked, so choosing
 *  one reads as editing it rather than starting over. */
export function askerChoices(askable: AskablePod[], links: PodLink[]): AskerChoice[] {
    return askable
        .filter((other) => other.throughYou)
        .map((other) => ({
            id: other.id,
            name: other.name,
            iconUrl: other.iconUrl,
            connected: links.some((link) => link.podId === other.id),
        }));
}

/** What is shared with a teammate already, to start the dialog from.
 *  Connecting again replaces what is shared, so starting from nothing would
 *  quietly take away whatever it read before. */
export function seededShares(links: PodLink[], askerId: string): LinkShare[] {
    return links.find((link) => link.podId === askerId)?.shared ?? [];
}

/** Whether something here is the person's own and not this teammate's to
 *  share: anything PERSONAL, and the `/me` folder and what is under it. The
 *  server refuses them too. */
export function isPersonal(item: Pick<LibraryItem, "path" | "visibility">): boolean {
    if ((item.visibility ?? "").toUpperCase() === "PERSONAL") return true;
    return item.path === "/me" || item.path.startsWith("/me/");
}

/** Whether everyone may already read it, connection or not. */
export function isPublic(item: Pick<LibraryItem, "visibility">): boolean {
    return (item.visibility ?? "").toUpperCase() === "PUBLIC";
}

/** One row in the "What it can read" list. */
export interface ShareRow {
    share: LinkShare;
    /** Already readable by anyone; ticking it changes nothing. */
    public: boolean;
    /** A folder that may hold folders of its own, to open. */
    folder: boolean;
}

/** The rows for one listing: the tables, or one folder's subfolders.
 *  Files are not shared one by one, and the person's own things are never
 *  offered. */
export function shareRows(items: LibraryItem[]): ShareRow[] {
    return items
        .filter((item) => (item.kind === "table" || item.kind === "folder") && !isPersonal(item))
        .map((item) => ({
            share: item.kind === "table" ? { kind: "table" as const, name: item.name } : { kind: "folder" as const, name: item.path },
            public: isPublic(item),
            folder: item.kind === "folder",
        }));
}

/** What is shared already but not in any list on screen -- a folder deeper
 *  than anything opened, a table past the last page that loaded. Still drawn,
 *  ticked, so saving keeps it unless somebody unticks it. */
export function sharesNotListed(chosen: LinkShare[], listed: LinkShare[]): LinkShare[] {
    return chosen.filter((one) => !listed.some((row) => sameShare(row, one)));
}

export function toggled(chosen: LinkShare[], one: LinkShare): LinkShare[] {
    return chosen.some((item) => sameShare(item, one)) ? chosen.filter((item) => !sameShare(item, one)) : [...chosen, one];
}

/** Every page of a listing, not the first fifty. Stops at a page token it has
 *  already followed, so a server that hands the same one back cannot loop. */
export async function everyPage<T>(read: (page?: string) => Promise<ResourcePage<T>>): Promise<T[]> {
    const items: T[] = [];
    const seen = new Set<string>();
    let page: string | undefined;
    for (;;) {
        const got = await read(page);
        items.push(...got.items);
        const next = got.next ?? undefined;
        if (!next || seen.has(next)) return items;
        seen.add(next);
        page = next;
    }
}
