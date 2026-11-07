import type { OpenTableRequest, Table, WebWidgetCreateRequest } from "lemma-sdk";
import type { LiveContext } from "../context";
import { readWidget } from "@web/data/contacts";
import { readOpening } from "@web/data/public-rows";
import type { TableShape } from "@web/library/relations";
import { accessOf, ROWS_PAGE, type Row, type TablesSource } from "./source";

/** Tables and records, from the server: the same calls the web makes
 *  (`lemma-frontend/src/data/live.ts`). */
export function tablesLive(context: LiveContext): TablesSource {
    const pod = (podId: string) => context.client(podId);
    return {
        async tables(podId, page) {
            const listed = await pod(podId).tables.list({ limit: 100, pageToken: page });
            return {
                items: (listed.items ?? []).map((table) => ({
                    name: table.name,
                    columns: typeof table.column_count === "number" ? table.column_count : null,
                    updatedAt: table.updated_at ?? null,
                    perUser: table.enable_rls === true,
                    access: accessOf(table.visibility, table.enable_rls === true),
                })),
                next: listed.next_page_token ?? null,
            };
        },

        async shape(podId, name) {
            return await pod(podId).tables.get(name) as Table;
        },

        async shapes(podId) {
            const listed = await pod(podId).tables.list({ limit: 100 });
            const names = (listed.items ?? []).map((table) => table.name);
            return Promise.all(names.map(async (name): Promise<TableShape> => {
                try { return await pod(podId).tables.get(name) as TableShape; } catch { return { name }; }
            }));
        },

        async rows(podId, name, page) {
            const result = await pod(podId).records.list(name, { limit: ROWS_PAGE, pageToken: page });
            return { items: (result.items ?? []) as Row[], next: result.next_page_token ?? null };
        },

        /* `datastore.query` takes SQL text and binds nothing, so a table name
           goes into it only when it is an ordinary identifier, as on the web. */
        async count(podId, name) {
            if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) return null;
            try {
                const answer = await pod(podId).datastore.query('SELECT count(*) AS n FROM "' + name + '"');
                const first = (answer.items ?? [])[0] as { n?: unknown } | undefined;
                const count = Number(first?.n);
                return Number.isFinite(count) ? count : null;
            } catch {
                return null;
            }
        },

        async record(podId, name, id) {
            return await pod(podId).records.get(name, id) as Row;
        },

        async referencing(podId, table, column, id, limit) {
            const page = await pod(podId).records.list(table, { filters: [{ field: column, op: "eq", value: id }], limit });
            return (page.items ?? []) as Row[];
        },

        async linkable(podId, table) {
            const page = await pod(podId).records.list(table, { limit: 200 });
            return (page.items ?? []) as Row[];
        },

        async create(podId, name, data) {
            return await pod(podId).records.create(name, data) as Row;
        },

        async update(podId, name, id, changes) {
            return await pod(podId).records.update(name, id, changes) as Row;
        },

        async remove(podId, name, id) {
            await pod(podId).records.delete(name, id);
        },

        async opening(podId, table) {
            return readOpening(await pod(podId).tables.publicRows.get(table));
        },

        async openTable(podId, table, audience, columns) {
            return readOpening(await pod(podId).tables.publicRows.open(table, { audience: audience as OpenTableRequest["audience"], columns }));
        },

        async closeTable(podId, table) {
            await pod(podId).tables.publicRows.close(table);
        },

        async webChats(podId) {
            return ((await pod(podId).contacts.widgets.list(podId)).items ?? []).map(readWidget);
        },

        async makeWebChat(podId, name, answer) {
            const made = await pod(podId).contacts.widgets.create(podId, { name, allowed_origins: [], answer: answer as WebWidgetCreateRequest["answer"] });
            return { ...readWidget(made), signingSecret: made.signing_secret };
        },
    };
}
