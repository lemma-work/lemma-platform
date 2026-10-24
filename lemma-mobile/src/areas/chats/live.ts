import * as FileSystem from "expo-file-system/legacy";
import type { SurfaceContactsConfig, UpdateTableRequest, WebWidgetCreateRequest, WebWidgetUpdateRequest } from "lemma-sdk";
import { uploadFile } from "../../data/io";
import { conversationRefOf, items, record, words } from "../../data/wire";
import { liveAvailable, podLive } from "../../live/pod-live";
import { freshAccess } from "../../session/auth";
import type { LiveContext } from "../context";
import type { ChatsSource, FilePart, LemmaPerson } from "./source";
import { attachmentFolder } from "@web/channels/compose-model";
import { everyPage, readContact, readContacts, readOrigins, readWidget, type OutreachSent } from "@web/data/contacts";
import { readChannelDetail, readChannelPage, readLine, readLinePage, readThreadPage, writeParts } from "@web/channels/model";
import { readSearchPage } from "@web/channels/search-model";
import { readSourceSettings } from "@web/channels/sources";
import { readInbox } from "@web/channels/waiting";
import { mineRequest, must, postRequest, readAgents, readSurfaces, roleOf, updateRequest } from "./wire";

/** Chats, groups, people and contacts, from the server: the calls the web
 *  makes (`lemma-frontend/src/channels/api.ts`, and `data/live.ts` for the
 *  Lemma's people, agents, bots and contacts). */

function lineFileUrl(api: string, podId: string, channelId: string, lineId: string, fileId: string): string {
    return `${api}/pods/${[podId, "channels", channelId, "messages", lineId, "files", fileId].map(encodeURIComponent).join("/")}`;
}

export function chatsLive(context: LiveContext): ChatsSource {
    const pod = (podId: string) => context.client(podId);
    const detail = (raw: unknown) => must(readChannelDetail(raw), "chat");
    const settings = (raw: unknown) => must(readSourceSettings(raw), "group");
    /* Each Lemma's name, read once: its front agent answers under it. */
    const names = new Map<string, Promise<string | null>>();
    const nameOf = (podId: string) => {
        const held = names.get(podId);
        if (held) return held;
        const read = pod(podId).pods.get(podId).then((row) => words(record(row).name)).catch(() => null);
        names.set(podId, read);
        return read;
    };

    return {
        async list(podId, { before, ...options }) {
            return readChannelPage(await pod(podId).channels.list(podId, { ...options, cursor: before } as never));
        },
        async get(podId, channelId) {
            return detail(await pod(podId).channels.get(podId, channelId));
        },
        async lines(podId, channelId, beforeSeq) {
            return readLinePage(await pod(podId).channels.messages(podId, channelId, { limit: 50, beforeSeq }));
        },
        async thread(podId, channelId, rootId, afterSeq) {
            return readThreadPage(await pod(podId).channels.thread(podId, channelId, rootId, { limit: 100, afterSeq }));
        },
        async post(podId, channelId, body) {
            return must(readLine(await pod(podId).channels.post(podId, channelId, postRequest(body))), "message");
        },
        async edit(podId, channelId, lineId, text, parts) {
            const body = parts ? { text, parts: writeParts(parts) } : text;
            return must(readLine(await pod(podId).channels.edit(podId, channelId, lineId, body as never)), "message");
        },
        async remove(podId, channelId, lineId) {
            await pod(podId).channels.deleteMessage(podId, channelId, lineId);
        },
        async share(podId, channelId, lineId) {
            return must(readLine(await pod(podId).channels.share(podId, channelId, lineId)), "message");
        },
        async markRead(podId, channelId, seq, threadRootId) {
            await pod(podId).channels.markRead(podId, channelId, seq, threadRootId);
        },
        async create(podId, body) {
            return detail(await pod(podId).channels.create(podId, body as never));
        },
        async update(podId, channelId, change) {
            return detail(await pod(podId).channels.update(podId, channelId, updateRequest(change) as never));
        },
        async destroy(podId, channelId) {
            await pod(podId).channels.delete(podId, channelId);
        },
        async addMembers(podId, channelId, body) {
            return detail(await pod(podId).channels.addMembers(podId, channelId, body as never));
        },
        async updateMember(podId, channelId, member, change) {
            await pod(podId).channels.updateMember(podId, channelId, member.kind as never, member.id, change as never);
        },
        async removeMember(podId, channelId, member) {
            await pod(podId).channels.removeMember(podId, channelId, member.kind as never, member.id);
        },
        async join(podId, channelId) {
            return detail(await pod(podId).channels.join(podId, channelId));
        },
        async leave(podId, channelId) {
            await pod(podId).channels.leave(podId, channelId);
        },
        async setMine(podId, channelId, change) {
            return detail(await pod(podId).channels.updateMyMembership(podId, channelId, mineRequest(change) as never));
        },
        async setAgentAnswers(podId, channelId, on) {
            return detail(await pod(podId).channels.setAgentAnswers(podId, channelId, on));
        },
        async retryDelivery(podId, channelId, lineId, deliveryId) {
            await pod(podId).channels.retryDelivery(podId, channelId, lineId, deliveryId);
        },
        async attach(podId, channelId, file, line): Promise<FilePart> {
            const written = record(await uploadFile(context.server, podId, { ...file, name: line.name }, {
                directoryPath: attachmentFolder(channelId, line.clientMessageId),
                searchEnabled: true,
            }));
            const path = words(written.path);
            if (!path) throw new Error(line.name + " came back without a place.");
            return { type: "file", path, name: words(written.name) ?? line.name, mimeType: file.type || null, size: file.size, fileId: null };
        },
        async lineFile(podId, channelId, lineId, file) {
            const access = await freshAccess();
            if (!access) throw new Error("Signed out.");
            const safe = file.name.replace(/[^\w.\- ]+/g, "_").slice(-120) || "file";
            const target = `${FileSystem.cacheDirectory}lemma-chat-${file.fileId}-${safe}`;
            const known = await FileSystem.getInfoAsync(target);
            if (known.exists) return target;
            const result = await FileSystem.downloadAsync(lineFileUrl(context.server.api, podId, channelId, lineId, file.fileId), target, {
                headers: { Authorization: `Bearer ${access}`, Accept: "*/*" },
            });
            if (result.status < 200 || result.status >= 300) {
                await FileSystem.deleteAsync(target, { idempotent: true });
                throw new Error(result.status === 404 ? file.name + " is not there any more." : file.name + " could not be read (" + result.status + ").");
            }
            return result.uri;
        },
        async answer(podId, channelId, lineId, given) {
            await pod(podId).channels.answer(podId, channelId, lineId, given);
        },
        async inbox(podId) {
            return readInbox(await pod(podId).channels.inbox(podId));
        },
        async search(podId, q, options) {
            return readSearchPage(await pod(podId).channels.search(podId, q, { ...options, limit: 30 }));
        },
        async online(podId) {
            if (!liveAvailable() || podLive(podId).status !== "open") return [];
            const result = record(await podLive(podId).call("presence", {}));
            return Array.isArray(result.online) ? result.online.filter((one): one is string => typeof one === "string") : [];
        },

        async source(podId, channelId, sourceId) {
            return settings(await pod(podId).channels.sources.get(podId, channelId, sourceId));
        },
        async updateSource(podId, channelId, sourceId, change) {
            return settings(await pod(podId).channels.sources.update(podId, channelId, sourceId, change as never));
        },
        async startWhatsAppGroup(podId, body) {
            return settings(await pod(podId).channels.sources.startWhatsAppGroup(podId, {
                surface_name: body.surfaceName,
                title: body.title,
                answers_outsiders: body.answersOutsiders,
            }));
        },
        async telegramLink(podId, surfaceName) {
            const link = await pod(podId).channels.sources.telegramLink(podId, surfaceName);
            return { url: link.url, expiresAt: link.expires_at };
        },
        async surfaces(podId) {
            const [listed, name] = await Promise.all([pod(podId).podSurfaces.list(podId, { limit: 30 } as never), nameOf(podId)]);
            return readSurfaces(listed, name);
        },

        async agents(podId) {
            const [listed, name] = await Promise.all([pod(podId).agents.list({ limit: 100 } as never), nameOf(podId)]);
            return readAgents(listed, name);
        },
        async people(podId): Promise<LemmaPerson[]> {
            const listed = await pod(podId).podMembers.list(podId);
            return items(listed).flatMap((raw): LemmaPerson[] => {
                const member = record(raw);
                const userId = words(member.user_id);
                if (!userId) return [];
                const email = words(member.email) ?? words(member.user_email);
                return [{ userId, name: words(member.user_name) ?? email ?? "Someone", email, role: roleOf(member.roles) }];
            });
        },
        async abilities(podId) {
            const held = record(await pod(podId).podPermissions.me(podId));
            return Array.isArray(held.actions) ? held.actions.filter((one): one is string => typeof one === "string") : [];
        },
        async conversations(podId, cursor, search) {
            const listed = record(await pod(podId).conversations.list({
                pod_id: podId,
                limit: 30,
                page_token: cursor ?? undefined,
                search: search || undefined,
                in_channel: false,
            } as never));
            return { items: items(listed).map(conversationRefOf), next: words(listed.next_page_token) };
        },
        async workflowRun(podId, runId) {
            return await pod(podId).workflows.runs.get(runId, podId) as never;
        },
        async submitForm(podId, runId, nodeId, inputs) {
            await pod(podId).workflows.runs.submitForm(runId, { inputs, node_id: nodeId } as never, podId);
        },

        async contacts(podId) {
            return everyPage(async (cursor) => readContacts(await pod(podId).contacts.list(podId, { limit: 200, cursor })));
        },
        async renameContact(podId, contactId, name) {
            return readContact(await pod(podId).contacts.rename(podId, contactId, name));
        },
        async forgetContact(podId, contactId) {
            await pod(podId).contacts.remove(podId, contactId);
        },
        async exportContact(podId, contactId) {
            return JSON.stringify(await pod(podId).contacts.export(podId, contactId), null, 2);
        },
        async followUp(podId, contactId, message, handle, clientMessageId): Promise<OutreachSent> {
            const sent = await pod(podId).contacts.outreach(podId, contactId, {
                handle: handle.value,
                kind: handle.kind as never,
                text: message,
                client_message_id: clientMessageId,
            });
            return {
                channelId: sent.line.channel_id,
                lineId: sent.line.id,
                platform: sent.delivery.platform ?? null,
                status: sent.delivery.status as OutreachSent["status"],
                reason: sent.delivery.reason ?? null,
            };
        },
        async widgets(podId) {
            return ((await pod(podId).contacts.widgets.list(podId)).items ?? []).map(readWidget);
        },
        async createWidget(podId, draft) {
            const made = await pod(podId).contacts.widgets.create(podId, {
                name: draft.name.trim(),
                allowed_origins: readOrigins(draft.origins),
                answer: draft.answer as WebWidgetCreateRequest["answer"],
            });
            return { ...readWidget(made), signingSecret: made.signing_secret };
        },
        async updateWidget(podId, widgetId, change) {
            return readWidget(await pod(podId).contacts.widgets.update(podId, widgetId, {
                ...(change.answer ? { answer: change.answer as WebWidgetUpdateRequest["answer"] } : {}),
            }));
        },
        async reissueWidget(podId, widgetId) {
            return (await pod(podId).contacts.widgets.reissue(podId, widgetId)).signing_secret;
        },
        async deleteWidget(podId, widgetId) {
            await pod(podId).contacts.widgets.remove(podId, widgetId);
        },
        async reach(podId) {
            const client = pod(podId);
            const [tables, functions] = await Promise.all([
                everyPage(async (pageToken) => {
                    const page = await client.tables.list({ limit: 100, pageToken });
                    return { items: page.items ?? [], next: page.next_page_token };
                }),
                everyPage(async (pageToken) => {
                    const page = await client.functions.list({ limit: 100, pageToken });
                    return { items: page.items ?? [], next: page.next_page_token };
                }),
            ]);
            return {
                tables: tables.map((table) => ({
                    name: table.name,
                    contactOwned: Boolean(table.contact_owned),
                    perPerson: Boolean(table.enable_rls),
                    contactColumns: (table as { contact_columns?: string[] }).contact_columns ?? [],
                })),
                functions: functions.map((fn) => ({
                    name: fn.name,
                    description: fn.description ?? null,
                    contactsInvoke: Boolean(fn.contacts_invoke),
                })),
            };
        },
        async setTableContactOwned(podId, table, on, columns) {
            const change = { contact_owned: on, ...(on && columns ? { contact_columns: columns } : {}) };
            await pod(podId).tables.update(table, change as UpdateTableRequest);
        },
        async setFunctionContactsInvoke(podId, fn, on) {
            await pod(podId).functions.setContactsInvoke(fn, on);
        },
        async shareableColumns(podId, table) {
            const columns = (await pod(podId).tables.get(table)).columns ?? [];
            return columns.filter((column) => !(column as { system?: boolean }).system && column.name !== "contact_id").map((column) => column.name);
        },
        async autoAcknowledge(podId) {
            const row = record(await pod(podId).pods.get(podId));
            const said = record(record(row.config).contacts).auto_acknowledge;
            return typeof said === "string" && said.trim() ? said : null;
        },
        async setAutoAcknowledge(podId, said) {
            await pod(podId).pods.update(podId, { config: { contacts: { auto_acknowledge: said } } } as never);
        },
        async botAnswer(podId, surfaceName) {
            const surface = record(await pod(podId).podSurfaces.get(podId, surfaceName));
            const saved = record(record(surface.config).contacts).answer;
            const own = surface.platform === "RESEND" || Boolean(words(surface.account_id)) || surface.credential_mode === "CUSTOM";
            return { answer: saved === "known" || saved === "anyone" ? saved : "off", own };
        },
        async setBotAnswer(podId, surfaceName, answer) {
            await pod(podId).podSurfaces.update(podId, surfaceName, { config: { contacts: { answer: answer as SurfaceContactsConfig["answer"] } } } as never);
        },
    };
}
