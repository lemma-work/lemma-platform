import type { Candidate } from "./matching";

/** Turning what each API returns into something rankable.
 *
 *  Pure, and separate from the fetching, because the shapes are where this
 *  quietly goes wrong: a list whose items have `name` and a list whose items
 *  have `title` both look fine until one of them silently produces a column of
 *  blank rows. Every reader here says what it expects and falls back to
 *  something a person can still recognise.
 */

/** A list response, whichever of the two shapes the API used. */
export function itemsOf(value: unknown): Record<string, unknown>[] {
    if (Array.isArray(value)) return value as Record<string, unknown>[];
    const items = (value as { items?: unknown })?.items;
    return Array.isArray(items) ? (items as Record<string, unknown>[]) : [];
}

function text(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

/** The best name an item has, in the order the APIs tend to carry them. */
export function nameOf(item: Record<string, unknown>, fallback = "Untitled"): string {
    return (
        text(item.name) ||
        text(item.title) ||
        text(item.display_name) ||
        text(item.slug) ||
        text(item.email) ||
        fallback
    );
}

function idOf(item: Record<string, unknown>, fallback: string): string {
    return text(item.id) || text(item.name) || fallback;
}

/** Columns worth searching for text.
 *
 *  Only the ones that can hold words. Searching an INTEGER or a UUID for a
 *  typed phrase costs a query per table and cannot match — and a VECTOR column
 *  is an embedding, which is neither readable nor cheap to scan.
 */
export const SEARCHABLE_COLUMN_TYPES = new Set(["TEXT", "ENUM", "FILE_PATH"]);

/** How many tables a single query is allowed to reach.
 *
 *  Records are the one source that costs a request *per table*, so an
 *  unbounded pod turns one keystroke into thirty round trips. Bounded, and the
 *  bound is visible in the results rather than silently applied — see
 *  `recordSourcesFrom`, which reports what it left out.
 */
export const MAX_RECORD_TABLES = 8;

export interface RecordSource {
    key: string;
    tableName: string;
    label: string;
    searchFields: string[];
    displayField?: string;
    limit: number;
}

/** Which tables a record search should actually ask, and what it skipped.
 *
 *  A table with no text column is skipped rather than queried: there is nothing
 *  in it a typed phrase could match, and asking anyway spends a round trip to
 *  be told so.
 */
export function recordSourcesFrom(
    tables: { name: string; columns?: { name: string; type?: string }[] }[],
    limit = MAX_RECORD_TABLES,
): { sources: RecordSource[]; skipped: number } {
    const usable: RecordSource[] = [];
    for (const table of tables) {
        const fields = (table.columns ?? [])
            .filter((column) => SEARCHABLE_COLUMN_TYPES.has(String(column.type ?? "").toUpperCase()))
            .map((column) => column.name);
        if (fields.length === 0) continue;
        usable.push({
            key: "table:" + table.name,
            tableName: table.name,
            label: table.name,
            searchFields: fields,
            /* The first text column is what a row gets called. It is a guess,
               and a reasonable one: tables put the human-readable column
               first far more often than not. */
            displayField: fields[0],
            limit: 5,
        });
    }
    return { sources: usable.slice(0, limit), skipped: Math.max(0, usable.length - limit) };
}

/* ── readers ───────────────────────────────────────────────────────
   One per kind. Each takes whatever the API returned and produces
   candidates; none of them throw on a shape they did not expect. */

export function agentCandidates(raw: unknown): Candidate[] {
    return itemsOf(raw).map((item, index) => ({
        kind: "agent" as const,
        id: idOf(item, "agent-" + index),
        title: nameOf(item, "Agent"),
        subtitle: text(item.description) || null,
        payload: item,
    }));
}

export function functionCandidates(raw: unknown): Candidate[] {
    return itemsOf(raw).map((item, index) => ({
        kind: "function" as const,
        id: idOf(item, "function-" + index),
        title: nameOf(item, "Function"),
        subtitle: text(item.description) || null,
        payload: item,
    }));
}

export function workflowCandidates(raw: unknown): Candidate[] {
    return itemsOf(raw).map((item, index) => ({
        kind: "workflow" as const,
        id: idOf(item, "workflow-" + index),
        title: nameOf(item, "Workflow"),
        subtitle: text(item.description) || null,
        payload: item,
    }));
}

export function appCandidates(raw: unknown): Candidate[] {
    return itemsOf(raw).map((item, index) => ({
        kind: "app" as const,
        id: idOf(item, "app-" + index),
        title: nameOf(item, "App"),
        subtitle: text(item.description) || null,
        payload: item,
    }));
}

export function scheduleCandidates(raw: unknown): Candidate[] {
    return itemsOf(raw).map((item, index) => ({
        kind: "schedule" as const,
        id: idOf(item, "schedule-" + index),
        title: nameOf(item, "Schedule"),
        /* What it runs and when are both worth finding by, and neither is the
           name — "every weekday" is a thing somebody would type. */
        subtitle: text(item.cron) || text(item.description) || null,
        haystack: text(item.cron) + " " + text(item.agent_name) + " " + text(item.function_name),
        payload: item,
    }));
}

export function tableCandidates(raw: unknown): Candidate[] {
    return itemsOf(raw).map((item, index) => ({
        kind: "table" as const,
        id: idOf(item, "table-" + index),
        title: nameOf(item, "Table"),
        subtitle: typeof item.column_count === "number" ? item.column_count + " columns" : null,
        payload: item,
    }));
}

/** People in the pod.
 *
 *  Flat, and none of the field names are the obvious ones. The row carries
 *  `user_name`, `user_email` and `roles` — not a nested `user` object, not
 *  `role`, and its key is `pod_member_id` rather than `id`. Guessing at the
 *  nested shape made every person in a real pod render as the word "Member",
 *  which is to say unfindable by name, which is to say the search box was
 *  lying about covering people.
 */
export function personCandidates(raw: unknown): Candidate[] {
    return itemsOf(raw).map((item, index) => {
        const full = text(item.user_name);
        const email = text(item.user_email) || text(item.email);
        const roles = Array.isArray(item.roles)
            ? (item.roles as unknown[]).map((role) => text(role).replace(/^POD_/, "").toLowerCase()).filter(Boolean)
            : [];
        return {
            kind: "person" as const,
            id: text(item.pod_member_id) || text(item.user_id) || "member-" + index,
            title: full || email || "Member",
            /* Their email under their name, or what they may do when that is
               all there is — a row saying only "Member" identifies nobody. */
            subtitle: full && email ? email : roles.join(", ") || null,
            haystack: email + " " + roles.join(" "),
            payload: item,
        };
    });
}

/** A user's typed text, safe to drop inside a single-quoted SQL literal.
 *
 *  Record search needs `col1 ILIKE x OR col2 ILIKE x`, and the datastore
 *  endpoint takes a SQL string with no parameter binding — so the escaping is
 *  this app's job and there is nowhere else to put it.
 *
 *  Doubling the quote is the whole of it under `standard_conforming_strings`,
 *  which has been on by default since PostgreSQL 9.1: a backslash is then an
 *  ordinary character and `''` is the only escape there is. NUL cannot appear
 *  in a Postgres text value at all, so it goes rather than being passed along
 *  to be rejected, and the length is capped because a search box is not a
 *  delivery mechanism for a megabyte.
 *
 *  The server is not relying on this — it permits one read-only SELECT, no
 *  mutations, no cross-schema reference, and RLS still scopes the rows. This is
 *  the near side of that, and it is written as though the far side were not
 *  there.
 */
export function sqlLiteral(raw: string): string {
    const cleaned = raw.replace(/\0/g, "").slice(0, 200);
    return "'" + cleaned.replace(/'/g, "''") + "'";
}

/** `%` and `_` are wildcards to LIKE, so a person typing them means them
 *  literally and gets a match on everything instead. Escaped against an
 *  explicit ESCAPE character, which then has to be escaped first. */
export function likePattern(raw: string): string {
    const escaped = raw.replace(/\\/g, "\\\\").replace(/[%_]/g, (char) => "\\" + char);
    return sqlLiteral("%" + escaped + "%");
}

/** One SELECT that searches every text column of a table.
 *
 *  Columns are names the server gave us, never anything typed, so they are
 *  quoted as identifiers and nothing else touches them.
 */
export function recordSearchSql(table: RecordSource, raw: string): string {
    const pattern = likePattern(raw);
    const where = table.searchFields
        .map((field) => `"${field.replace(/"/g, '""')}"::text ILIKE ${pattern} ESCAPE '\\'`)
        .join(" OR ");
    return `SELECT * FROM "${table.tableName.replace(/"/g, '""')}" WHERE ${where} LIMIT ${table.limit}`;
}
