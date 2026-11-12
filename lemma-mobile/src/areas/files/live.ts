import { readText, saveToPhone } from "../../data/io";
import { readSchedule } from "@web/schedule/schedules";
import { displayAgentName, isPodDefaultAgent } from "@web/data/agent-names";
import type { LiveContext } from "../context";
import { hitsOf, INLINE_HTML_LIMIT, INLINE_TEXT_LIMIT, itemOf, kindOf, nameOf, parentOf, readsInline, sharedLinkOf, wakeRunOf } from "./model";
import type { CommentRow, FileContent, FilesSource, Mentionable } from "./source";
import { createFrom, forget, onDisk, readBase64, replaceFrom, textType } from "./transfer";
import { COMMENTS_TABLE, readComment } from "@web/docpages/comments/model";
import { readingProblem } from "@web/library/file-status";
import { instructionsFor } from "./web/page-chat";
import { createOrReplace } from "@web/library/replace-on-conflict";
import { ATTACHED_FILE_KEY, findQuery, RESOURCE_KEY, resourceKey, resourceTitle } from "@web/thread/resource-conversation";
import { wakeRequest } from "@web/docpages/comments/wake";

/** Files, pages and docs, from the server: the SDK where it works on a
 *  phone, the platform's own upload where it needs a Blob (`transfer.ts`). */

type Raw = Record<string, unknown>;

const record = (value: unknown): Raw => (value && typeof value === "object" && !Array.isArray(value) ? (value as Raw) : {});
const itemsOf = (value: unknown): Raw[] => {
    const items = record(value).items;
    return Array.isArray(items) ? items.map(record) : [];
};
const status = (error: unknown) => (error as { statusCode?: number } | null)?.statusCode;

/** The comments table's shape, as the web makes it (`docpages/comments/store.ts`). */
const COMMENT_COLUMNS = [
    { name: "file_path", type: "TEXT", required: true, description: "The page this comment is on." },
    { name: "quote", type: "TEXT", description: "The passage it is about; empty for the page as a whole." },
    { name: "quote_prefix", type: "TEXT", description: "A little of the text just before the passage." },
    { name: "quote_suffix", type: "TEXT", description: "A little of the text just after the passage." },
    { name: "body", type: "TEXT", required: true, description: "What was said." },
    { name: "parent_id", type: "TEXT", description: "The comment this replies to; empty for a new thread." },
    { name: "resolved", type: "BOOLEAN", default: false, description: "The thread is done." },
    { name: "written_by", type: "USER", description: "Who wrote it." },
    { name: "author_name", type: "TEXT", description: "Their name as shown, for readers who cannot look the person up." },
    { name: "author_agent", type: "TEXT", description: "The bot that wrote it, when a bot replied." },
    { name: "mentioned_agent", type: "TEXT", description: "The bot this comment asks for, by name. A schedule wakes that bot." },
];

export function filesLive(context: LiveContext): FilesSource {
    const { server } = context;
    const client = (podId: string) => context.client(podId);

    async function listComments(podId: string, path: string): Promise<CommentRow[]> {
        const listed = await client(podId).records.list(COMMENTS_TABLE, {
            filters: [{ field: "file_path", op: "eq", value: path }],
            sort: [{ field: "created_at", direction: "asc" }],
            limit: 500,
        } as never);
        return itemsOf(listed).map(readComment).filter((row): row is CommentRow => row !== null);
    }

    async function pageConversation(podId: string, path: string): Promise<{ id: string } | null> {
        const found = await client(podId).request<{ items?: { id: string }[] }>("GET", `/pods/${podId}/conversations`, { params: findQuery("file", path) });
        return found?.items?.[0] ?? null;
    }

    return {
        async list(podId, directory, page) {
            const result = await client(podId).files.list({ directoryPath: directory, limit: 50, pageToken: page });
            return { items: itemsOf(result).map(itemOf), next: record(result).next_page_token as string | null | undefined };
        },

        async search(podId, query, within) {
            const result = await client(podId).files.search(query, { limit: 25, scopeMode: "SUBTREE", scopePath: within === "/" ? null : within });
            return hitsOf(itemsOf(result) as never);
        },

        async read(podId, path) {
            const files = client(podId).files;
            /* The signed link is asked for only for what is drawn from one;
               text is read inline and never uses it. */
            const inline = readsInline(kindOf("", path));
            const [detail, urls] = await Promise.all([
                files.get(path) as Promise<Raw>,
                inline ? Promise.resolve({} as Raw) : (files.getUrl(path) as Promise<unknown> as Promise<Raw>).catch(() => ({} as Raw)),
            ]);
            const mime = typeof detail.mime_type === "string" ? detail.mime_type : "";
            const size = typeof detail.size_bytes === "number" ? detail.size_bytes : 0;
            const kind = kindOf(mime, path);
            const base: FileContent = {
                name: typeof detail.name === "string" ? detail.name : nameOf(path),
                path: typeof detail.path === "string" ? detail.path : path,
                mime,
                size,
                kind,
                appUrl: typeof urls.app_url === "string" ? urls.app_url : undefined,
                rawUrl: typeof urls.url === "string" ? urls.url : undefined,
            };
            if (!readsInline(kind)) return base;
            if (size > (kind === "html" ? INLINE_HTML_LIMIT : INLINE_TEXT_LIMIT)) return { ...base, kind: "binary", note: "Too big to show here" };
            try {
                const text = await readText(server, podId, path);
                if (text === null) return { ...base, kind: "binary", note: "This file could not be read" };
                return { ...base, text };
            } catch {
                return { ...base, kind: "binary", note: "This file could not be read" };
            }
        },

        async bytes(podId, path) {
            const uri = await saveToPhone(server, podId, path);
            try {
                return await readBase64(uri);
            } finally {
                forget(uri);
            }
        },

        async stamp(podId, path) {
            const detail = record(await client(podId).files.get(path));
            return {
                sha: typeof detail.content_sha256 === "string" ? detail.content_sha256 : null,
                updatedAt: typeof detail.updated_at === "string" ? detail.updated_at : null,
            };
        },

        async signedUrl(podId, path) {
            const urls = record(await client(podId).files.getUrl(path));
            return typeof urls.url === "string" ? urls.url : null;
        },

        async why(podId, path) {
            try {
                return readingProblem(record(await client(podId).files.get(path)).last_processing_error as string | null);
            } catch {
                return readingProblem(null);
            }
        },

        toPhone: (podId, path) => saveToPhone(server, podId, path),

        async upload(podId, directory, file) {
            const path = (directory === "/" ? "" : directory) + "/" + file.name;
            const made = await createOrReplace(
                () => createFrom(server, podId, file, directory),
                () => replaceFrom(server, podId, path, file),
            );
            return itemOf({ size_bytes: file.size ?? undefined, mime_type: file.type, ...made });
        },

        async uploadBytes(podId, directory, file, searchEnabled) {
            const made = await onDisk(file.name, file.base64, "base64", (uri) => createFrom(server, podId, { uri, name: file.name, type: file.type }, directory, searchEnabled));
            return itemOf(made);
        },

        async makeFolder(podId, parent, name) {
            const made = await client(podId).files.folder.create(name, { directoryPath: parent });
            return itemOf({ kind: "FOLDER", ...record(made) });
        },

        async move(podId, from, to) {
            await client(podId).files.update(from, { newPath: to });
        },

        async remove(podId, path) {
            await client(podId).files.delete(path);
        },

        async retry(podId, path) {
            const read = record(await client(podId).files.retryProcessing(path));
            return typeof read.status === "string" ? read.status : "PENDING";
        },

        async writeText(podId, path, text) {
            await onDisk(nameOf(path), text, "utf8", async (uri) => {
                try {
                    await replaceFrom(server, podId, path, { uri, type: textType(path) });
                } catch (error) {
                    /* An update only edits a file that exists. A new page is
                       uploaded instead, which also makes its folders. */
                    if (status(error) !== 404) throw error;
                    await createFrom(server, podId, { uri, name: nameOf(path), type: textType(path) }, parentOf(path));
                }
            });
        },

        async createText(podId, path, text) {
            await onDisk(nameOf(path), text, "utf8", (uri) => createFrom(server, podId, { uri, name: nameOf(path), type: textType(path) }, parentOf(path)));
        },

        async tableNames(podId) {
            return itemsOf(await client(podId).tables.list({ limit: 100 })).map((one) => String(one.name ?? "")).filter(Boolean);
        },

        async tableColumns(podId, table) {
            const shape = record(await client(podId).tables.get(table));
            return (Array.isArray(shape.columns) ? shape.columns : []).map(record).map((one) => ({ name: String(one.name ?? "") }));
        },

        async tableRows(podId, table) {
            const page = record(await client(podId).records.list(table, { limit: 50 }));
            return { items: itemsOf(page), next: page.next_page_token as string | null | undefined };
        },

        async runQuery(podId, sql) {
            const answer = record(await client(podId).datastore.query(sql));
            return { items: itemsOf(answer), truncated: answer.truncated === true };
        },

        async appUrl(podId, path) {
            const urls = record(await client(podId).files.getUrl(path));
            return typeof urls.app_url === "string" ? urls.app_url : null;
        },

        async links(podId, path) {
            /* The listing is everyone's links you made in this Lemma, so it is
               walked and filtered. Bounded: past a thousand, the newest matter. */
            const out = [];
            let cursor: string | undefined;
            for (let page = 0; page < 10; page++) {
                const listed = record(await client(podId).files.listSignedUrls({ limit: 100, cursor }));
                for (const link of (Array.isArray(listed.links) ? listed.links : []).map(record)) {
                    if (link.path === path) out.push(sharedLinkOf(server.web, link as never));
                }
                if (typeof listed.next_page_token !== "string" || !listed.next_page_token) break;
                cursor = listed.next_page_token;
            }
            return out;
        },

        async share(podId, path, bounds) {
            return sharedLinkOf(server.web, record(await client(podId).files.createSignedUrl(path, bounds)) as never);
        },

        async revoke(podId, code) {
            return record(await client(podId).files.revokeSignedUrl(code)).revoked === true;
        },

        async pageConversation(podId, path) {
            return (await pageConversation(podId, path))?.id ?? null;
        },

        async movePageConversation(podId, from, to) {
            /* As the web's `moveDocThread`: found under the old name before the
               new one is looked up, or the lookup finds nothing. */
            const found = await client(podId).request<{ items?: { id: string; metadata?: Raw | null }[] }>("GET", `/pods/${podId}/conversations`, { params: findQuery("file", from) });
            const thread = found?.items?.[0];
            if (!thread) return;
            const metadata = { ...(thread.metadata ?? {}), [RESOURCE_KEY]: resourceKey("file", to), [ATTACHED_FILE_KEY]: to };
            await client(podId).request("PATCH", `/pods/${podId}/conversations/${thread.id}`, { body: { metadata } });
        },

        async askAboutPage(podId, path, text) {
            const sdk = client(podId);
            let id = (await pageConversation(podId, path))?.id ?? null;
            if (!id) {
                /* Begun on the first thing said, as the web begins it: bound to
                   the file, so the next ask finds it, and told about the page. */
                const made = await sdk.conversations.create({
                    pod_id: podId,
                    title: resourceTitle("file", path),
                    type: "PROJECT",
                    instructions: instructionsFor({ kind: "file", name: path }),
                    metadata: { [RESOURCE_KEY]: resourceKey("file", path), [ATTACHED_FILE_KEY]: path },
                } as never);
                id = made.id;
            }
            await sdk.conversations.appendMessage(id, { content: text } as never, { pod_id: podId });
            return id;
        },

        async commentsStatus(podId) {
            try {
                await client(podId).tables.get(COMMENTS_TABLE);
                return "ready";
            } catch (error) {
                if (status(error) === 404) return "missing";
                if (status(error) === 403) return "forbidden";
                throw error;
            }
        },

        async enableComments(podId) {
            await client(podId).tables.create({
                name: COMMENTS_TABLE,
                columns: COMMENT_COLUMNS,
                enable_rls: false,
                config: { description: "Comments on pages. One row per comment; a reply names its thread in parent_id." },
            } as never);
        },

        comments: listComments,

        async addComment(podId, data, author) {
            const signed = { ...data, ...(author.id ? { written_by: author.id } : {}), author_name: author.name };
            let made: unknown;
            try {
                made = await client(podId).records.create(COMMENTS_TABLE, signed);
            } catch (error) {
                /* A table made before those columns, by someone who could not
                   add them: the comment still goes, unsigned. */
                if (status(error) !== 422 && status(error) !== 400) throw error;
                made = await client(podId).records.create(COMMENTS_TABLE, data);
            }
            const row = readComment(record(made).data ?? made);
            if (!row) throw new Error("The comment was saved but came back unreadable.");
            return row;
        },

        async updateComment(podId, id, patch) {
            await client(podId).records.update(COMMENTS_TABLE, id, patch);
        },

        async deleteComment(podId, id) {
            await client(podId).records.delete(COMMENTS_TABLE, id);
        },

        async moveComments(podId, from, to) {
            const rows = await listComments(podId, from);
            await Promise.all(rows.map((row) => client(podId).records.update(COMMENTS_TABLE, row.id, { file_path: to })));
        },

        async mentionable(podId, lemmaName) {
            const [agents, members] = await Promise.all([
                client(podId).agents.list({ limit: 100 }).catch(() => ({ items: [] })),
                client(podId).podMembers.list(podId, { limit: 200 }).catch(() => ({ items: [] })),
            ]);
            const out: Mentionable[] = [];
            for (const agent of itemsOf(agents)) {
                const name = String(agent.name ?? "");
                const kind = typeof agent.kind === "string" ? agent.kind : null;
                if (!name || agent.takes_input === true) continue;
                const front = isPodDefaultAgent(name, kind);
                out.push({ kind: "agent", key: front ? "POD_DEFAULT" : name, label: front ? lemmaName : displayAgentName(name, kind) });
            }
            out.sort((a, b) => Number(b.key === "POD_DEFAULT") - Number(a.key === "POD_DEFAULT") || a.label.localeCompare(b.label));
            for (const member of itemsOf(members)) {
                const userId = String(member.user_id ?? "");
                const name = String(member.user_name || member.user_email || member.email || "").trim();
                if (userId && name) out.push({ kind: "person", key: userId, label: name });
            }
            return out;
        },

        async notify(podId, userId, text) {
            await client(podId).channels.notify(podId, { to: userId, text });
        },

        async wakes(podId) {
            return itemsOf(await client(podId).schedules.list({ limit: 100 })).map(readSchedule);
        },

        async wakeRuns(podId, jobId) {
            const runs = await client(podId).request("GET", `/pods/${podId}/schedules/${jobId}/runs`, { params: { limit: 20, skipped: false } });
            return (Array.isArray(runs) ? runs.map(record) : itemsOf(runs)).map(wakeRunOf);
        },

        async turnOnWake(podId, agent, job) {
            if (job && (!job.active || job.pausedByFailures)) await client(podId).schedules.update(job.id, { is_active: true } as never);
            else if (!job) await client(podId).request("POST", `/pods/${podId}/schedules`, { body: wakeRequest(agent.key, agent.label) });
        },

        async askAgain(podId, id, agentKey) {
            await client(podId).records.update(COMMENTS_TABLE, id, { mentioned_agent: null });
            await client(podId).records.update(COMMENTS_TABLE, id, { mentioned_agent: agentKey });
        },
    };
}
