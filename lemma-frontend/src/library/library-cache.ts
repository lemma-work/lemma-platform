import type { LibraryItem, ResourcePage } from "@/data";

/** Edits to the cached pages of a directory listing.
 *
 *  The listing is an infinite query, so its cache is pages rather than a list,
 *  and a write has to reach into whichever page the item is on. Doing it here
 *  keeps the paging out of the components and makes the interesting part — an
 *  upload lands at the top, a rename stays where it was, a delete leaves a hole
 *  rather than a shifted page — something a test can hold.
 *
 *  Every function returns the input when nothing changed.
 */

export interface Pages {
    pages: ResourcePage<LibraryItem>[];
    pageParams: unknown[];
}

/** A new file or folder, at the top of the first page.
 *
 *  The top, because the listing sorts folders first and then by recency, and
 *  something just made is the most recent thing there is. It is also where the
 *  person is looking: they pressed the button.
 */
export function withItem(cache: Pages | undefined, item: LibraryItem): Pages | undefined {
    if (!cache || cache.pages.length === 0) return cache;
    const [first, ...rest] = cache.pages;
    /* A second upload of the same name replaces rather than duplicates — the
       server overwrote the file, so two rows would be one file said twice. */
    const without = first.items.filter((existing) => existing.path !== item.path);
    return { ...cache, pages: [{ ...first, items: [item, ...without] }, ...rest] };
}

export function withoutItem(cache: Pages | undefined, path: string): Pages | undefined {
    if (!cache) return cache;
    let changed = false;
    const pages = cache.pages.map((page) => {
        const items = page.items.filter((item) => item.path !== path);
        if (items.length === page.items.length) return page;
        changed = true;
        return { ...page, items };
    });
    return changed ? { ...cache, pages } : cache;
}

export function withRenamedItem(
    cache: Pages | undefined,
    path: string,
    name: string,
    nextPath: string,
): Pages | undefined {
    if (!cache) return cache;
    let changed = false;
    const pages = cache.pages.map((page) => {
        let touched = false;
        const items = page.items.map((item) => {
            if (item.path !== path) return item;
            touched = true;
            changed = true;
            return { ...item, name, path: nextPath };
        });
        return touched ? { ...page, items } : page;
    });
    return changed ? { ...cache, pages } : cache;
}

/** Where a rename puts something: same directory, new last segment. */
export function renamedPath(path: string, name: string): string {
    const parts = path.replace(/\/+$/, "").split("/");
    parts[parts.length - 1] = name;
    return parts.join("/") || "/";
}

/** Is this a name the pod can hold?
 *
 *  A slash would move the file rather than rename it, which is a different act
 *  with different consequences and should not happen because somebody typed a
 *  character. The rest are the names that break a path for everybody who reads
 *  it later.
 */
export function nameProblem(name: string): string | null {
    const trimmed = name.trim();
    if (!trimmed) return "A name is required.";
    if (trimmed.includes("/")) return "A name cannot contain a slash.";
    if (trimmed === "." || trimmed === "..") return "That name is reserved.";
    if (trimmed.length > 255) return "That name is too long.";
    return null;
}

/** The path a new item gets, given the directory it was made in. */
export function pathIn(directory: string, name: string): string {
    const base = directory.replace(/\/+$/, "");
    return (base === "" ? "" : base) + "/" + name.trim();
}
