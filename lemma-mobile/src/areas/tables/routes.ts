import { lemmaTab, part, underLemma } from "../paths";

/** Tables and records. Other areas link here (a table in a chat, a table in
 *  a Lemma's Recently), so these three stay as they are:
 *
 *  - `tables`: a Lemma's page on its Tables tab;
 *  - `table`: `(in)/lemma/[podId]/table/[name]`, the rows in their layout;
 *  - `record`: `(in)/lemma/[podId]/record/[table]/[id]`, one row and its links.
 *
 *  `edit` (a new row, or a change to one) and `collect` (people outside
 *  adding rows) are reached from those two screens only. */
export const tablesRoutes = {
    tables: (podId: string) => lemmaTab(podId, "tables"),
    table: (podId: string, name: string) => underLemma(podId, "table", name),
    record: (podId: string, table: string, id: string) => underLemma(podId, "record", table, id),
    /** A row to change, by its id; none for a new one, with whatever the place
     *  it was added from implies (`preset`: added under "doing", it is doing). */
    edit: (podId: string, table: string, options: { id?: string; preset?: Record<string, unknown> } = {}) => {
        const query = [
            options.id ? `id=${part(options.id)}` : "",
            options.preset && Object.keys(options.preset).length ? `preset=${part(JSON.stringify(options.preset))}` : "",
        ].filter(Boolean).join("&");
        return underLemma(podId, "edit", table) + (query ? `?${query}` : "");
    },
    collect: (podId: string, table: string) => underLemma(podId, "collect", table),
};

/** The same place, with the back link naming where it goes back to, for a
 *  table or a row opened from another row rather than from its table. */
export function withBack(href: string, label: string): string {
    return href + (href.includes("?") ? "&" : "?") + `back=${part(label)}`;
}
