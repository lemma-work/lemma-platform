import type { LiveContext } from "../../context";
import { askableFrom, connectBody, fileItem, linksFrom, tableItem } from "./model";
import type { WorksWithSource } from "./source";

/** The same calls the web makes (`data/live.ts`: `askablePods`, `podLinks`,
 *  `connectPod`, `disconnectPod`, `listLibrary`). */
export function worksWithLive(context: LiveContext): WorksWithSource {
    const pod = (podId: string) => context.client(podId);
    const at = (podId: string, tail: string) => `/pods/${encodeURIComponent(podId)}/${tail}`;
    return {
        async askable(podId) {
            return askableFrom(await pod(podId).request("GET", at(podId, "askable-pods")));
        },
        async links(podId) {
            return linksFrom(await pod(podId).request("GET", at(podId, "pod-links")));
        },
        async connect(podId, askingPodId, shares) {
            await pod(podId).request("PUT", at(podId, `pod-links/${encodeURIComponent(askingPodId)}`), { body: connectBody(shares) });
        },
        async disconnect(podId, askingPodId) {
            await pod(podId).request("DELETE", at(podId, `pod-links/${encodeURIComponent(askingPodId)}`));
        },
        async shareable(podId, kind, directory, page) {
            const client = pod(podId);
            if (kind === "tables") {
                const listed = await client.tables.list({ limit: 50, pageToken: page });
                return { items: listed.items.map((one) => tableItem(one as Parameters<typeof tableItem>[0])), next: listed.next_page_token ?? null };
            }
            const listed = await client.files.list({ directoryPath: directory, limit: 50, pageToken: page });
            return { items: listed.items.map((one) => fileItem(one as Parameters<typeof fileItem>[0])), next: listed.next_page_token ?? null };
        },
    };
}
