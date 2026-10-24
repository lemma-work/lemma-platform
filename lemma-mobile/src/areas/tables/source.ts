import type { Table } from "lemma-sdk";
import type { NewWebWidget, WebWidget } from "@web/data/contacts";
import type { Audience, TableOpening } from "@web/data/public-rows";
import type { Row } from "@web/library/record-cache";
import type { TableShape } from "@web/library/relations";

/** Tables and records (PARITY.md, B).
 *
 *  Everything these screens read or change, behind one interface with a
 *  live side (`live.ts`) and a sample side (`sample.ts`). Screens reach it
 *  only through `useArea("tables")`, never the SDK directly, so sample data
 *  and a live account are the same screens. */

export type { Row };

/** One of a Lemma's tables, as its list says it. */
export interface TableItem {
    name: string;
    columns: number | null;
    updatedAt: string | null;
    /** Each person sees only their own rows (row-level security). */
    perUser: boolean;
    /** Who can open it, as the platform records it (the web's `all-view.tsx`). */
    access: TableAccess;
}

/** `space` is everyone in the Lemma; `rows` is a table everyone opens where
 *  each person sees only their own rows. */
export type TableAccess = "space" | "personal" | "restricted" | "public" | "rows";

export function accessOf(visibility: string | null | undefined, perUser: boolean): TableAccess {
    const said = (visibility ?? "").toUpperCase();
    if (said === "PUBLIC") return "public";
    if (said === "PERSONAL") return "personal";
    if (said === "RESTRICTED") return "restricted";
    return perUser ? "rows" : "space";
}

export interface Page<T> {
    items: T[];
    next: string | null;
}

/** Rows of a table read a page at a time. A hundred is twice the web's page
 *  and a tenth of what the server allows: a phone pays per request more than
 *  per row. */
export const ROWS_PAGE = 100;

export interface TablesSource {
    tables(podId: string, page?: string): Promise<Page<TableItem>>;
    /** One table's schema: columns, primary key, row-level security. */
    shape(podId: string, name: string): Promise<Table>;
    /** Every table's columns, for what points back at a row. */
    shapes(podId: string): Promise<TableShape[]>;
    rows(podId: string, name: string, page?: string): Promise<Page<Row>>;
    /** How many rows the table holds, or null when the Lemma will not say. */
    count(podId: string, name: string): Promise<number | null>;
    record(podId: string, name: string, id: string): Promise<Row>;
    /** Rows of `table` whose `column` holds `id`: what points back here. */
    referencing(podId: string, table: string, column: string, id: string, limit: number): Promise<Row[]>;
    /** Rows of a table to link to from a form, the first page of them. */
    linkable(podId: string, table: string): Promise<Row[]>;
    create(podId: string, name: string, data: Row): Promise<Row>;
    update(podId: string, name: string, id: string, changes: Row): Promise<Row>;
    remove(podId: string, name: string, id: string): Promise<void>;

    /** Collecting responses: who outside may add rows, and which columns. */
    opening(podId: string, table: string): Promise<TableOpening>;
    openTable(podId: string, table: string, audience: Audience, columns: string[]): Promise<TableOpening>;
    closeTable(podId: string, table: string): Promise<void>;
    /** The Lemma's web chats, which are also the door a form goes through. */
    webChats(podId: string): Promise<WebWidget[]>;
    makeWebChat(podId: string, name: string, answer: WebWidget["answer"]): Promise<NewWebWidget>;
}
