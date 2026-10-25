import * as FileSystem from "expo-file-system/legacy";
import { readText, uploadFile } from "../../data/io";
import { authedFetch, freshAccess } from "../../session/auth";
import { items, record, words } from "../../data/wire";
import { fileKind, INLINE_TEXT_LIMIT, readableSize } from "../../thread/previews";
import { readChoice, readRuntime, runtimePayload, type Runtime } from "../../thread/runtimes";
import type { LiveContext } from "../context";
import type { ChatSource, ShownFile } from "./source";

/** The chat thread's reads and writes, from the server: the same calls the
 *  web's `data/live.ts` makes for a chat's cards (`tableRows`, `runQuery`,
 *  `readFile`), its model picker (`listRuntimes`, `defaultRuntime`,
 *  `getPodRuntime`, `get/setConversationRuntime`), and renaming and
 *  archiving (`conversations.update`). A file's words are read from the
 *  response itself (`readText`), since React Native cannot always read the
 *  Blob the SDK's download answers. */
export function chatLive(context: LiveContext): ChatSource {
    const { server, client } = context;
    return {
        async tableRows(podId, name) {
            const page = await client(podId).records.list(name, { limit: 50 });
            return { items: (page.items ?? []) as Record<string, unknown>[], truncated: false };
        },
        async runQuery(podId, sql) {
            const answer = await client(podId).datastore.query(sql);
            return { items: (answer.items ?? []) as Record<string, unknown>[], truncated: answer.truncated === true };
        },
        async file(podId, path): Promise<ShownFile> {
            const files = client(podId).files;
            const guess = fileKind("", path);
            const inline = guess === "markdown" || guess === "text" || guess === "html";
            const [detail, urls] = await Promise.all([
                files.get(path) as Promise<{ name?: string; path?: string; mime_type?: string | null; size_bytes?: number }>,
                inline ? Promise.resolve({} as { url?: string }) : (files.getUrl(path) as Promise<{ url?: string }>).catch(() => ({} as { url?: string })),
            ]);
            const mime = detail.mime_type ?? "";
            const size = detail.size_bytes ?? 0;
            const kind = fileKind(mime, path);
            const pages = Number(record((detail as { metadata?: unknown }).metadata).page_count);
            const shown: ShownFile = {
                name: detail.name ?? path.split("/").pop() ?? path,
                path: detail.path ?? path,
                kind,
                size,
                mime,
                url: urls.url ?? null,
                text: null,
                note: null,
                pages: Number.isFinite(pages) && pages > 0 ? pages : null,
            };
            if (kind !== "markdown" && kind !== "text") return shown;
            if (size > INLINE_TEXT_LIMIT) return { ...shown, note: "Too big to show here · " + readableSize(size) };
            try {
                return { ...shown, text: (await readText(server, podId, shown.path)) ?? "" };
            } catch {
                return { ...shown, note: "It could not be read from here." };
            }
        },
        async firstPage(podId, path) {
            /* Rendered by the server on demand, fetched with this phone's
               session into its cache: the picture needs no headers after. */
            const access = await freshAccess();
            if (!access) return null;
            const child = `${path.replace(/\/$/, "")}/pages/page_0001.jpg`;
            const url = `${server.api}/pods/${encodeURIComponent(podId)}/datastore/files/children/content?path=${encodeURIComponent(child)}`;
            const target = `${FileSystem.cacheDirectory}lemma-page-${encodeURIComponent(podId + path).replace(/%/g, "_").slice(-120)}.jpg`;
            const got = await FileSystem.downloadAsync(url, target, { headers: { Authorization: `Bearer ${access}`, Accept: "image/*" } });
            return got.status >= 200 && got.status < 300 ? got.uri : null;
        },
        async converted(podId, path) {
            const child = `${path.replace(/\/$/, "")}/document.md`;
            const response = await authedFetch(`${server.api}/pods/${encodeURIComponent(podId)}/datastore/files/children/content?path=${encodeURIComponent(child)}`, { headers: { Accept: "*/*" } });
            return response.ok ? await response.text() : null;
        },
        async runtimes(orgId) {
            const listed = await client().agentRuntime.listProfiles(orgId, { includeDisabled: true });
            return items(listed).map(readRuntime).filter((one): one is Runtime => one !== null);
        },
        async inheritedRuntime(orgId) {
            const listed = await client().agentRuntime.listRuntimes(orgId);
            return readChoice(record(listed).default_runtime);
        },
        async podRuntime(podId) {
            const pod = record(await client(podId).pods.get(podId));
            const config = record(pod.config);
            /* The stored runtime carries the model; `default_profile_id` is a
               legacy mirror that never does, so it is the fallback. */
            return readChoice(config.default_runtime) ?? readChoice({ profile_id: config.default_profile_id });
        },
        async agentPinsRuntime(podId, agentId) {
            const page = await client(podId).agents.list({ limit: 100 });
            return items(page).some((raw) => record(raw).id === agentId && record(raw).has_pinned_runtime === true);
        },
        async conversationRuntime(podId, conversationId) {
            const conversation = record(await client(podId).conversations.get(conversationId, { pod_id: podId }));
            return readChoice(conversation.agent_runtime);
        },
        async setConversationRuntime(podId, conversationId, choice) {
            await client(podId).conversations.update(conversationId, { agent_runtime: runtimePayload(choice) } as never, { pod_id: podId });
        },
        async rename(podId, conversationId, title) {
            await client(podId).conversations.update(conversationId, { title } as never, { pod_id: podId });
        },
        async archive(podId, conversationId) {
            await client(podId).conversations.update(conversationId, { is_archived: true } as never, { pod_id: podId });
        },
        async workingFolder(podId, conversationId) {
            const conversation = record(await client(podId).conversations.get(conversationId, { pod_id: podId }));
            return words(conversation.pod_cwd);
        },
        async upload(podId, file, directory) {
            /* One at a time, and searchable, so the agent can find it by what
               is in it as well as by the name in the message. The endpoint
               makes any missing folder on the way. */
            const written = record(await uploadFile(server, podId, file, { directoryPath: directory, searchEnabled: true }));
            const path = words(written.path);
            if (!path) throw new Error(`${file.name} was uploaded, but the server did not say where it went.`);
            return { name: words(written.name) ?? file.name, path };
        },
    };
}
