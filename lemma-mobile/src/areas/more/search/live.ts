import type { LiveContext } from "../../context";
import { findQuery, RESOURCE_KEY, resourceInstructions, resourceKey, resourceTitle } from "@web/thread/resource-conversation";
import type { SearchSource } from "./source";
import { itemsOf, recordSearchSql } from "@web/search/sources";

/** The same calls the web's search makes (`search/use-search.ts`), and the
 *  web's `useResourceConversation` for a function's chat. */
export function searchLive(context: LiveContext): SearchSource {
    const pod = (podId: string) => context.client(podId);
    return {
        async functions(podId) {
            return await pod(podId).functions.list({ limit: 100 });
        },
        async apps(podId) {
            return await pod(podId).apps.list({ limit: 100 });
        },
        async files(podId, query, limit) {
            return await pod(podId).files.search(query, { limit, searchMethod: "HYBRID" });
        },
        async records(podId, table, query) {
            /* One statement per table rather than one per column: the records
               endpoint ANDs its filters and has no OR. The words are escaped
               by `recordSearchSql`, and the endpoint runs one read-only SELECT
               under row-level security. */
            return itemsOf(await pod(podId).datastore.query(recordSearchSql(table, query)));
        },
        async discuss(podId, kind, name) {
            const client = pod(podId);
            const found = await client.request<{ items?: { id: string }[] }>("GET", `/pods/${encodeURIComponent(podId)}/conversations`, { params: findQuery(kind, name) });
            const existing = found?.items?.[0]?.id;
            if (existing) return existing;
            const made = await client.conversations.create({
                pod_id: podId,
                title: resourceTitle(kind, name),
                type: "PROJECT",
                /* Told once, when it is made, as the web tells it. */
                instructions: resourceInstructions(kind, name),
                metadata: { [RESOURCE_KEY]: resourceKey(kind, name) },
            } as Parameters<typeof client.conversations.create>[0]);
            return made.id;
        },
    };
}
