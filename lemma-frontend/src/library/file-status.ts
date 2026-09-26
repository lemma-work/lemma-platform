import type { LibraryItem } from "@/data";
import type { Pages } from "./library-cache";

/** What the library says about a file's reading, from the datastore status.
 *
 *  Quiet on purpose: a file that was read says nothing, because that is what
 *  every file is supposed to be. Only the two states a person can do something
 *  about, or should wait for, get words — "Reading…" while the teammate cannot
 *  search it yet, and "Couldn't read" with a Retry when it failed. Anything
 *  else (a folder, a file that is never indexed, a status this build does not
 *  know) says nothing rather than guessing.
 */
export type FileReading =
    | { state: "reading"; label: string }
    | { state: "failed"; label: string };

export function fileReading(status: string | null | undefined): FileReading | null {
    switch ((status ?? "").toUpperCase()) {
        case "PENDING":
        case "PROCESSING":
            return { state: "reading", label: "Reading…" };
        case "FAILED":
        case "FAILED_PERMANENT":
            return { state: "failed", label: "Couldn’t read" };
        default:
            return null;
    }
}

/** What to show when the error itself is not available — the list does not
 *  carry it, and the detail read can fail too. */
export function readingProblem(error: string | null | undefined): string {
    const said = (error ?? "").trim();
    return said || "Lemma could not read this file. Retry, or upload it again.";
}

/** One row's status, changed in place — a retry sets it back to reading
 *  without refetching every page the person has scrolled through. */
export function withItemStatus(cache: Pages | undefined, path: string, status: string): Pages | undefined {
    if (!cache) return cache;
    let changed = false;
    const pages = cache.pages.map((page) => {
        if (!page.items.some((item) => item.path === path && item.status !== status)) return page;
        changed = true;
        return { ...page, items: page.items.map((item): LibraryItem => (item.path === path ? { ...item, status } : item)) };
    });
    return changed ? { ...cache, pages } : cache;
}
