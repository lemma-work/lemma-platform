import type { ResourcePage } from "@/data";

/** Edits to the cached pages of a table's rows.
 *
 *  The rows are an infinite query, so the cache is pages rather than a list and
 *  a write has to find the page its row is on. Invalidating instead would
 *  refetch every page somebody has scrolled through to reflect one cell.
 */

export type Row = Record<string, unknown>;

export interface RowPages {
    pages: ResourcePage<Row>[];
    pageParams: unknown[];
}

/** What identifies a row, as the table itself declares it.
 *
 *  Not assumed to be `id`. A pod can name its primary key anything, and a
 *  delete aimed at the wrong column is a delete aimed at nothing — or, worse,
 *  an update that writes over a different row.
 */
export function idOf(row: Row, primaryKey: string): string | null {
    const value = row[primaryKey];
    if (value === null || value === undefined) return null;
    return String(value);
}

/** A new row, at the top of the first page.
 *
 *  The top, because it is what just happened and what the person is looking
 *  for. The server's ordering will put it wherever it belongs on the next
 *  refetch; until then, showing it where it can be seen beats showing it in the
 *  right place and off the bottom of the list.
 */
export function withRow(cache: RowPages | undefined, row: Row): RowPages | undefined {
    if (!cache || cache.pages.length === 0) return cache;
    const [first, ...rest] = cache.pages;
    return { ...cache, pages: [{ ...first, items: [row, ...first.items] }, ...rest] };
}

/** One row, replaced wherever it sits. */
export function withUpdatedRow(
    cache: RowPages | undefined,
    primaryKey: string,
    id: string,
    next: Row,
): RowPages | undefined {
    if (!cache) return cache;
    let changed = false;
    const pages = cache.pages.map((page) => {
        let touched = false;
        const items = page.items.map((row) => {
            if (idOf(row, primaryKey) !== id) return row;
            touched = true;
            changed = true;
            /* Merged rather than replaced: an update response carries what the
               server wrote, which for a partial update is not every column. */
            return { ...row, ...next };
        });
        return touched ? { ...page, items } : page;
    });
    return changed ? { ...cache, pages } : cache;
}

export function withoutRow(cache: RowPages | undefined, primaryKey: string, id: string): RowPages | undefined {
    if (!cache) return cache;
    let changed = false;
    const pages = cache.pages.map((page) => {
        const items = page.items.filter((row) => idOf(row, primaryKey) !== id);
        if (items.length === page.items.length) return page;
        changed = true;
        return { ...page, items };
    });
    return changed ? { ...cache, pages } : cache;
}

/** How a value reads in a cell.
 *
 *  Shared with the read-only view it grew out of, so an edited row looks the
 *  same as the one beside it the moment it is saved rather than on the next
 *  refetch.
 */
export function cellText(value: unknown): string {
    if (value === null || value === undefined) return "—";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
}

/** Columns that are about the row rather than in it. */
const BOOKKEEPING = new Set(["id", "created_at", "updated_at", "creator_user_id", "sort_order", "pod_id", "user_id"]);

/** Columns worth naming a row after, in the order they are worth it. */
const NAMING = ["name", "title", "label", "summary", "subject", "headline"];

const LOOKS_LIKE_A_TIMESTAMP = /^\d{4}-\d{2}-\d{2}[T ]/;
const LOOKS_LIKE_A_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** What a row is called when something has to name it — a confirmation, mostly.
 *
 *  Not simply "the first short string". Key order is the server's, and on a
 *  real table it put `created_at` first: the delete confirmation read "Delete
 *  2026-09-17T20:45:37.523110Z?", which names a moment rather than a row and
 *  tells nobody what is about to go.
 *
 *  So: a column that is plainly a name, then any other readable text that is
 *  not bookkeeping and not a machine value, then the key as a last resort.
 */
export function rowLabel(row: Row, primaryKey: string): string {
    /* Long is not the same as unusable. A title of sixty-two characters is
       still that row's name, and a length cap that rejects it sends the search
       on to the next column — which on a real table named a row "post" after
       its format. Shorten the name; do not swap it for a different field. */
    const shorten = (value: string): string => (value.length <= 60 ? value : value.slice(0, 59).trimEnd() + "…");

    const readable = (value: unknown): string | null => {
        if (typeof value !== "string") return null;
        const trimmed = value.trim();
        if (!trimmed) return null;
        if (LOOKS_LIKE_A_TIMESTAMP.test(trimmed) || LOOKS_LIKE_A_UUID.test(trimmed)) return null;
        return trimmed;
    };

    for (const preferred of NAMING) {
        const found = readable(row[preferred]);
        if (found) return shorten(found);
    }
    /* Past the columns that are plainly names, length does matter again: an
       arbitrary text column could be an essay, and the first sixty characters
       of one identify nothing. */
    for (const [key, value] of Object.entries(row)) {
        if (key === primaryKey || BOOKKEEPING.has(key)) continue;
        const found = readable(value);
        if (found && found.length <= 60) return found;
    }
    return idOf(row, primaryKey) ?? "this row";
}
