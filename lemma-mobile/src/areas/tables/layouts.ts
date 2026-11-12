import { matching, ordered, type Filters, type Ordering } from "@web/library/filters";
import { chooseForm, orderedFields, profileTable, rankedFields, type ColumnProfile, type FormChoice, type TableProfile } from "@web/library/profile";
import { dayStart, readableNumber, whenever } from "@web/library/reading";
import { cellText, idOf, withRow, withUpdatedRow, withoutRow, type Row, type RowPages } from "@web/library/record-cache";

/** A table, read the way the web's `TableView` reads it
 *  (`lemma-frontend/src/library/library.tsx`), as one pure step so a test can
 *  pin it: the shape chosen from the rows, the columns ranked, and the one
 *  answer to "which rows am I looking at" that search, filters and order give
 *  together. The drawing is the phone's own (`views.tsx`). */

/** How many rows are read in full to decide what a table is. Above it nothing
 *  more is fetched up front and the rows are shown as plain rows. */
export const WHOLE = 1000;

/** Rows drawn at once in plain rows, and in a layout before "Show more". */
export const DRAWN = 150;
export const PAGEFUL = 120;
/** Cards in one pile of a board before it stops. */
export const PILE = 50;

export interface TableRead {
    /** The columns, ranked best first: what the rows view shows. */
    fields: string[];
    profile: TableProfile | null;
    choice: FormChoice | null;
    /** The layout in use, or null for plain rows. */
    shaped: FormChoice | null;
    /** The columns plain rows show, in order. */
    visible: string[];
    roles: Map<string, string>;
    /** The rows asked for, in the order asked for. */
    matches: Row[];
    /** Nothing more will change the shape: read whole, or past what is read whole. */
    settled: boolean;
    complete: boolean;
    /** The table is small enough to read whole, so the rest is being fetched. */
    readWhole: boolean;
}

export function readTable(input: {
    declared: { name: string; system?: boolean }[] | undefined;
    rows: Row[];
    primaryKey: string;
    /** The row count, null when the Lemma will not say, undefined while asking. */
    count: number | null | undefined;
    hasNext: boolean;
    asRows: boolean;
    chosen: string[] | null;
    filters: Filters;
    search: string;
    order: Ordering | null;
    now?: Date;
}): TableRead {
    const { rows, count } = input;
    const fields = input.declared ?? Array.from(new Set(rows.flatMap(Object.keys))).map((name) => ({ name }));
    const readWhole = count !== null && count !== undefined && count <= WHOLE;
    const complete = count !== null && count !== undefined ? rows.length >= count : !input.hasNext;
    const settled = count === null || count === undefined ? !input.hasNext : complete || !readWhole;
    const profile = settled && rows.length ? profileTable(fields, rows, { primaryKey: input.primaryKey, complete, now: input.now }) : null;
    const choice = profile ? chooseForm(profile) : null;
    const shaped = choice && choice.form !== "grid" && !input.asRows ? choice : null;
    const ranked = profile ? orderedFields(profile).map((column) => column.name) : null;
    const visible = input.chosen ?? ranked ?? fields.map((field) => field.name);
    const roles = new Map((profile?.columns ?? []).map((column) => [column.name, column.role] as const));
    const matches = ordered(matching(rows, { ...input.filters, text: input.search }, input.now?.getTime()), input.order, roles);
    return { fields: ranked ?? fields.map((field) => field.name), profile, choice, shaped, visible, roles, matches, settled, complete, readWhole };
}

/** The values a schema fixes, by column: an ENUM's options. */
export function fixedValues(columns: { name: string; type?: string; options?: string[] | null }[] | undefined): Map<string, string[]> {
    return new Map((columns ?? []).filter((column) => column.type === "ENUM" && column.options?.length).map((column) => [column.name, column.options ?? []] as const));
}

/** The footer: one sentence, whichever of three things is true. */
export function countLine(asking: boolean, matched: number, loaded: number, count: number | null | undefined): string {
    if (asking) return `${matched} of ${loaded} rows match`;
    if (typeof count === "number" && count > loaded) return `${loaded} of ${count.toLocaleString()} rows loaded`;
    return loaded === 1 ? "1 row" : `${loaded} rows`;
}

/** The columns worth putting beside the one a layout is built around
 *  (`forms.tsx` `supporting`): ranked, and never prose, the key or
 *  bookkeeping. On a phone not a link either: the web prints its id, which on
 *  a card this narrow names nothing; the row's own page shows what it is. */
export function supporting(profile: TableProfile, around: string | undefined, limit: number): ColumnProfile[] {
    return rankedFields(profile)
        .filter((c) => c.name !== around && c.role !== "key" && c.role !== "bookkeeping" && c.role !== "prose" && c.role !== "link")
        .slice(0, limit);
}

/** The supporting fields minus whichever one the heading already says
 *  (`forms.tsx` `besides`). */
export function besides(row: Row, fields: ColumnProfile[], heading: string): ColumnProfile[] {
    const plain = heading.replace(/…$/, "").trim().toLowerCase();
    return fields.filter((c) => {
        const value = row[c.name];
        if (value === null || value === undefined || value === "") return false;
        const text = cellText(value).replace(/…$/, "").trim().toLowerCase();
        if (!text) return false;
        return !(text.startsWith(plain) || plain.startsWith(text));
    });
}

/** A value as a layout prints it: a date said in words, a big number with
 *  separators, a tick as a word. */
export function valueText(row: Row, column: ColumnProfile, now = new Date()): string {
    const value = row[column.name];
    if (column.role === "number") return readableNumber(value) ?? cellText(value);
    if (column.role === "date") return whenever(value, now)?.text ?? cellText(value);
    if (column.role === "boolean") return value === true ? column.name.replace(/_/g, " ") : "not " + column.name.replace(/_/g, " ");
    return cellText(value);
}

/** A value in plain rows: a date as it would be written down, a number with
 *  separators past ten thousand (`library.tsx` `gridText`). */
export function cellFor(value: unknown, role: string | undefined): string {
    if ((role === "date" || role === "bookkeeping") && /^\d{4}-\d{2}-\d{2}/.test(String(value))) {
        const at = dayStart(value);
        if (Number.isFinite(at)) return new Date(at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
    }
    if (role === "number") return readableNumber(value) ?? cellText(value);
    if (role === "boolean") return value === true ? "Yes" : value === false ? "No" : "—";
    return cellText(value);
}

/** The piles of a board, in a stable order: first appearance, and a value
 *  seen later joins the end, so moving a card never slides the board under
 *  the thumb. `held` is the order kept so far. */
export function pileOrder(held: string[], values: string[]): string[] {
    let next = held;
    for (const value of values) if (!next.includes(value)) next = [...next, value];
    return next;
}

/** Days of a timeline, newest first, each with its events. */
export function timelineDays(rows: Row[], when: string, now = new Date()): { day: string; said: string | null; rows: Row[] }[] {
    const sorted = [...rows].sort((a, b) => dayStart(b[when]) - dayStart(a[when]));
    const days: { day: string; said: string | null; rows: Row[] }[] = [];
    for (const row of sorted) {
        const day = String(row[when] ?? "").slice(0, 10);
        const last = days[days.length - 1];
        if (last && last.day === day) last.rows.push(row);
        else days.push({ day, said: whenever(row[when], now)?.text ?? null, rows: [row] });
    }
    return days;
}

/** A checklist's rows: what is left first, then by when, and how many are left. */
export function checklistOrder(rows: Row[], when: string, done: (row: Row) => boolean): { rows: Row[]; left: number } {
    const sorted = [...rows].sort((a, b) => Number(done(a)) - Number(done(b)) || dayStart(a[when]) - dayStart(b[when]));
    return { rows: sorted, left: sorted.filter((row) => !done(row)).length };
}

/** One measure over time, for a chart: the points in order, without the ones
 *  that are not numbers. */
export function series(rows: Row[], when: string, measure: string): { at: number; value: number; label: string }[] {
    return [...rows]
        .sort((a, b) => dayStart(a[when]) - dayStart(b[when]))
        .map((row) => ({ at: dayStart(row[when]), value: Number(row[measure]), label: String(row[when] ?? "").slice(0, 10) }))
        .filter((point) => Number.isFinite(point.value) && Number.isFinite(point.at));
}

/** A row just made, put in the cached pages: at the top, or merged where it
 *  already is when the socket's insert got there first, which on a slow line
 *  it does. Says whether it was added, so the count moves only once. */
export function withMade(pages: RowPages | undefined, primaryKey: string, made: Row): { pages: RowPages | undefined; added: boolean } {
    const id = idOf(made, primaryKey);
    const there = Boolean(id) && Boolean(pages?.pages.some((page) => page.items.some((row) => idOf(row, primaryKey) === id)));
    if (there && id) return { pages: withUpdatedRow(pages, primaryKey, id, made), added: false };
    return { pages: withRow(pages, made), added: Boolean(pages && pages.pages.length) };
}

/** A change from the pod's live socket (`records` topic). */
export interface RecordChange {
    op: "insert" | "update" | "delete";
    table: string;
    record_id: string;
    row: Row | null;
}

/** The cached pages with the socket's changes applied: a new row at the top,
 *  a changed one merged where it sits, a deleted one gone. Also says by how
 *  much the row count moved. */
export function applyChanges(pages: RowPages | undefined, primaryKey: string, changes: RecordChange[]): { pages: RowPages | undefined; moved: number } {
    let next = pages;
    let moved = 0;
    for (const change of changes) {
        if (!next) break;
        const id = change.record_id;
        const held = next.pages.some((page) => page.items.some((row) => idOf(row, primaryKey) === id));
        if (change.op === "delete") {
            if (held) { next = withoutRow(next, primaryKey, id); moved -= 1; }
            continue;
        }
        if (!change.row) continue;
        if (held) next = withUpdatedRow(next, primaryKey, id, change.row);
        else { next = withRow(next, change.row); if (change.op === "insert") moved += 1; }
    }
    return { pages: next, moved };
}

