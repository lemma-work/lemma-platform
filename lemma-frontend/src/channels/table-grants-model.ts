/* What a group's people outside the space may do with one of its tables,
   without the drawing: the grant as read, how it is said, and the choice of
   columns. Pure, so the phone runs it where it lives as the web does
   (`table-grants.tsx` draws it here). */

export interface TableGrant {
    table: string;
    readColumns: string[];
    addColumns: string[];
}

/** One grant as the server sends it. */
export function readTableGrant(raw: { table: string; read_columns?: string[] | null; add_columns?: string[] | null }): TableGrant {
    return { table: raw.table, readColumns: raw.read_columns ?? [], addColumns: raw.add_columns ?? [] };
}

/** "read title, version · add a row with title". */
export function grantSaid(grant: Pick<TableGrant, "readColumns" | "addColumns">): string {
    const parts = [];
    if (grant.readColumns.length) parts.push("read " + grant.readColumns.join(", "));
    if (grant.addColumns.length) parts.push("add a row with " + grant.addColumns.join(", "));
    return parts.join(" · ");
}

/** The set with `name` added, or taken out when it was in. */
export function toggled(set: ReadonlySet<string>, name: string): Set<string> {
    const next = new Set(set);
    if (next.has(name)) next.delete(name); else next.add(name);
    return next;
}

/** A table, and at least one column to read or to add with. */
export function mayGive(table: string, read: ReadonlySet<string>, add: ReadonlySet<string>): boolean {
    return Boolean(table) && (read.size > 0 || add.size > 0);
}

/** The server's refusal of a grant, said: who may give one, and of what. */
export const GIVE_REFUSED = "Only the group’s owners or an admin can give it a table, and only one whose settings and rows they hold.";
