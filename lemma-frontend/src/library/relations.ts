/** What a row is attached to, read from the schema rather than guessed.
 *
 *  A record on its own is a bag of values; what makes it worth a page is what
 *  it connects to. Both directions matter and they are found differently:
 *  outward is declared on this table's own columns, inward is declared on
 *  everybody else's.
 */

export interface Column {
    name: string;
    type?: string;
    foreign_key?: { references?: string } | null;
}

export interface TableShape {
    name: string;
    primary_key_column?: string;
    columns?: Column[];
}

/** A column on this table that points at a row somewhere else. */
export interface OutwardLink {
    /** The column holding the reference. */
    column: string;
    table: string;
    /** The column it points at — usually, but not always, the primary key. */
    referencedColumn: string;
}

/** A column on *another* table that points back at this one. */
export interface InwardLink {
    table: string;
    column: string;
    referencedColumn: string;
}

/** `other_table.primary_key_column`, as the schema writes it.
 *
 *  Parsed rather than split blindly: a malformed or empty reference is a column
 *  with no usable link, and following it would mean querying a table called
 *  `""`.
 */
export function parseReference(reference: string | null | undefined): { table: string; column: string } | null {
    if (typeof reference !== "string") return null;
    const at = reference.indexOf(".");
    if (at <= 0 || at === reference.length - 1) return null;
    const table = reference.slice(0, at).trim();
    const column = reference.slice(at + 1).trim();
    if (!table || !column) return null;
    return { table, column };
}

/** Where this table's own columns point. */
export function outwardLinks(table: TableShape | null | undefined): OutwardLink[] {
    const links: OutwardLink[] = [];
    for (const column of table?.columns ?? []) {
        const target = parseReference(column.foreign_key?.references);
        if (!target) continue;
        links.push({ column: column.name, table: target.table, referencedColumn: target.column });
    }
    return links;
}

/** Which other tables point back here.
 *
 *  Needs every table's shape, because the reference is declared on the table
 *  doing the pointing and never on the one pointed at. A table that references
 *  this one twice — two columns, two meanings, `owner_id` and `approver_id` —
 *  is two links and not one, which is why these are keyed by column.
 */
export function inwardLinks(all: TableShape[], podTable: string): InwardLink[] {
    const links: InwardLink[] = [];
    const target = podTable.toLowerCase();
    for (const table of all) {
        if (table.name.toLowerCase() === target) continue;
        for (const column of table.columns ?? []) {
            const reference = parseReference(column.foreign_key?.references);
            if (!reference || reference.table.toLowerCase() !== target) continue;
            links.push({ table: table.name, column: column.name, referencedColumn: reference.column });
        }
    }
    return links;
}

/** What a linked row should be called when it is shown as a chip.
 *
 *  The same problem the delete confirmation had: a foreign key is a uuid, and
 *  "01a0b12c…" as the whole of a link tells nobody what is on the other end.
 *
 *  The five names tried here are the same short list that told a pipeline of
 *  companies it had "no title column" — real tables keep the thing a row is in
 *  `company`, `account`, `vendor`. So when none of them hits, `rowLabel` takes
 *  over, which reads the row rather than guessing at its column names.
 */
export function linkLabel(
    row: Record<string, unknown> | null | undefined,
    fallback: string,
    name: (row: Record<string, unknown>) => string = () => fallback,
): string {
    if (!row) return fallback;
    for (const key of ["name", "title", "label", "summary", "email"]) {
        const value = row[key];
        if (typeof value === "string" && value.trim()) return value.trim();
    }
    const read = name(row);
    return read && read !== "this row" ? read : fallback;
}

/** How many rows to show per inward link before saying there are more.
 *
 *  A record page is a summary of what a row is attached to, not a table viewer.
 *  Something with four hundred referencing rows needs the table, and the page
 *  says so rather than trying to be one.
 */
export const LINKED_ROWS_SHOWN = 5;
