/** Narrowing a table, and putting it in an order.
 *
 *  Both are the same reading again. The grid has always had one control — a box
 *  that matches text anywhere in a row — which cannot express "the ones that are
 *  still open", "mine", or "overdue", and those are most of what anybody wants
 *  to ask a table. What a column *is* says what you can ask of it: a status
 *  offers its values, a date offers when, a tick offers both of its states, and
 *  a name offers nothing a search box does not already do.
 *
 *  Pure, and apart from the view, because "does this row match" and "which of
 *  these comes first" both have right answers worth pinning down.
 */

import type { ColumnProfile, TableProfile } from "./profile";
import { rankedFields } from "./profile";
import { dayStart } from "./reading";
import { cellText, type Row } from "./record-cache";

/** When, in the words somebody would use rather than a pair of dates. */
export type Span = "any" | "overdue" | "today" | "week" | "later" | "past";

export interface Filters {
    /** Column → the values kept. An empty set keeps everything. */
    values: Record<string, string[]>;
    /** Column → which side of now. */
    spans: Record<string, Span>;
    /** Free text, across every column, as the box has always done. */
    text: string;
}

export const NO_FILTERS: Filters = { values: {}, spans: {}, text: "" };

export interface Ordering {
    column: string;
    /** Newest and largest first is what people mean by "sort by date". */
    down: boolean;
}

/** The columns worth offering as a filter, and what each one offers.
 *
 *  A column with a value of its own on every row — a name, an id, a note — is
 *  not a filter, it is the search box. What is left is the small set of things
 *  a row can be *one of*. */
export function filterable(profile: TableProfile): ColumnProfile[] {
    return rankedFields(profile).filter((c) =>
        (c.role === "enum" && c.values.length > 1)
        || c.role === "boolean"
        || (c.role === "date" && c.filled >= 0.5));
}

/** The columns worth offering as an order. Everything a person could mean by
 *  "sort by", which is most things that are not a paragraph. */
export function orderable(profile: TableProfile): ColumnProfile[] {
    return rankedFields(profile).filter((c) => c.role !== "prose" && c.role !== "structured");
}

function inSpan(value: unknown, span: Span, now: number): boolean {
    if (span === "any") return true;
    const at = dayStart(value);
    if (!Number.isFinite(at)) return false;
    const midnight = new Date(now); midnight.setHours(0, 0, 0, 0);
    const days = Math.round((at - midnight.getTime()) / 86_400_000);
    switch (span) {
        case "overdue": return days < 0;
        case "today": return days === 0;
        case "week": return days >= 0 && days <= 7;
        case "later": return days > 7;
        case "past": return days < 0;
    }
}

export function matching(rows: Row[], filters: Filters, now = Date.now()): Row[] {
    const wanted = Object.entries(filters.values).filter(([, keep]) => keep.length > 0);
    const spans = Object.entries(filters.spans).filter(([, span]) => span !== "any");
    const text = filters.text.trim().toLowerCase();

    return rows.filter((row) => {
        for (const [column, keep] of wanted) {
            if (!keep.includes(cellText(row[column]))) return false;
        }
        for (const [column, span] of spans) {
            if (!inSpan(row[column], span, now)) return false;
        }
        /* Unchanged on purpose: the box has always matched anywhere in a row,
           and somebody typing half a company name means that. */
        if (text && !Object.values(row).some((v) => cellText(v).toLowerCase().includes(text))) return false;
        return true;
    });
}

/** In order, with the empties last whichever way it is pointing.
 *
 *  A column sorted descending puts its blanks first if nothing says otherwise,
 *  so the top of the table becomes the rows that do not have the thing it was
 *  just sorted by. */
export function ordered(rows: Row[], order: Ordering | null, roles: Map<string, string>): Row[] {
    if (!order) return rows;
    const role = roles.get(order.column);
    const empty = (v: unknown) => v === null || v === undefined || v === "";

    const key = (row: Row): number | string | null => {
        const value = row[order.column];
        if (empty(value)) return null;
        if (role === "date") { const at = dayStart(value); return Number.isFinite(at) ? at : null; }
        if (role === "number") { const n = Number(value); return Number.isFinite(n) ? n : null; }
        if (role === "boolean") return value === true ? 1 : 0;
        return cellText(value).toLowerCase();
    };

    return [...rows].sort((a, b) => {
        const left = key(a), right = key(b);
        if (left === null && right === null) return 0;
        if (left === null) return 1;
        if (right === null) return -1;
        if (left === right) return 0;
        const ahead = left < right ? -1 : 1;
        return order.down ? -ahead : ahead;
    });
}

/** Whether anything has actually been asked, so the view can say so. */
export function isAsking(filters: Filters): boolean {
    return Boolean(filters.text.trim())
        || Object.values(filters.values).some((keep) => keep.length > 0)
        || Object.values(filters.spans).some((span) => span !== "any");
}
