import { MentionPart, NotifyLevel as WireNotifyLevel, type LemmaClient } from "lemma-sdk";
import { source } from "@/data";
import { lemma } from "@/session/client";
import {
    readChannelDetail,
    readChannelPage,
    readLine,
    readLinePage,
    readThreadPage,
    writeParts,
    type AgentAnswers,
    type Channel,
    type ChannelDetail,
    type ContactStatus,
    type Line,
    type LinePage,
    type MemberRole,
    type NotifyLevel,
    type Page,
    type Part,
    type ThreadPage,
    type Visibility,
} from "./model";
import { readSearchPage, type SearchHit } from "./search-model";
import type { ListFilter } from "./views";
import { readInbox, type Inbox, type PersonRef } from "./waiting";
import { readSourceSettings, type SourceChange, type SourceSettings } from "./sources";
import type { Mention } from "./mentions";
import { attachmentFolder } from "./compose-model";

/** A group one bot is in, with whether the reader is in it: an admin is
 *  listed the bot's private groups they are not in, to join on request. */
export type BotGroup = SourceSettings & { joined: boolean };

/** A change to a channel: absent is unchanged, `null` clears. */
export interface ChannelChange {
    title?: string | null;
    topic?: string | null;
    archived?: boolean;
    visibility?: Visibility;
    /** A group's: whether agents' answers are read by everybody in it. */
    answersShared?: boolean;
    agentsAnswer?: boolean;
    handBackAfterHours?: number;
    /** `me`, a user id, or null to unassign. */
    assignee?: string | null;
    status?: ContactStatus;
}

/** The reader's own settings in a channel. */
export interface MineChange {
    notify?: NotifyLevel;
    /** Platforms to hear of it on; null is the pod's rule. */
    notifyVia?: string[] | null;
    /** Muted until then; null unmutes. */
    mutedUntil?: string | null;
}

/** Everything the Channels place asks of the API, read into what it draws.
 *
 *  One seam, so the sample source can answer the same calls from its
 *  fixtures: channels are the caller's own view of the pod -- what is unread
 *  is theirs -- and were never part of `PodSource`. */
export interface ChannelApi {
    list(options: ListFilter & { contactId?: string; before?: string; limit?: number }): Promise<Page<Channel>>;
    get(channelId: string): Promise<ChannelDetail>;
    /** The newest page of top-level lines, or the one before line `beforeSeq`. */
    lines(channelId: string, beforeSeq?: number): Promise<LinePage>;
    /** A page of a thread's replies, oldest first: the first page, or the
     *  one after reply `afterSeq`. */
    thread(channelId: string, rootId: string, afterSeq?: number): Promise<ThreadPage>;
    post(channelId: string, body: PostBody): Promise<Line>;
    /** With `parts`, they replace the line's: who it names, and what it
     *  shares. Without, they stay as they are. */
    edit(channelId: string, lineId: string, text: string, parts?: readonly Part[]): Promise<Line>;
    remove(channelId: string, lineId: string): Promise<void>;
    /** Share an agent's answer made with the reader's access with everybody
     *  in the channel (`canShare` on the line). */
    share(channelId: string, lineId: string): Promise<Line>;
    /** The reader has seen the channel up to line `seq`. */
    /** Read the timeline to `seq` -- or, with `threadRootId`, that thread. */
    markRead(channelId: string, seq: number, threadRootId?: string | null): Promise<void>;
    create(body: { title: string; topic: string | null; members: string[]; visibility: Visibility; agents?: { id: string; answers: AgentAnswers }[] }): Promise<ChannelDetail>;
    update(channelId: string, change: ChannelChange): Promise<ChannelDetail>;
    /** Delete a group made in Lemma, and its timeline. */
    destroy(channelId: string): Promise<void>;
    addMembers(channelId: string, body: { people?: string[]; agents?: { id: string; answers: AgentAnswers }[] }): Promise<ChannelDetail>;
    /** A person's role, or when an agent answers. */
    updateMember(channelId: string, member: Pick<PersonRef, "kind"> & { id: string }, change: { role?: MemberRole; answers?: AgentAnswers }): Promise<void>;
    /** Take somebody out of a group. */
    removeMember(channelId: string, member: Pick<PersonRef, "kind"> & { id: string }): Promise<void>;
    /** The reader joins a channel they may see -- or, as an admin, a bot's
     *  private group on request. */
    join(channelId: string): Promise<ChannelDetail>;
    /** The reader leaves a channel they are in. */
    leave(channelId: string): Promise<void>;
    setMine(channelId: string, change: MineChange): Promise<ChannelDetail>;
    /** A contact's channel: switch its agent on (handing them back now) or off. */
    setAgentAnswers(channelId: string, on: boolean): Promise<ChannelDetail>;
    retryDelivery(channelId: string, lineId: string, deliveryId: string): Promise<void>;
    /** A file for a line to share: uploaded to the writer's own files, in
     *  the line's own folder (`clientMessageId`) under `name`, and named as
     *  a part. The server checks the writer may read it and keeps its own
     *  copy of the part. */
    attach(channelId: string, file: File, line: { clientMessageId: string; name: string }): Promise<Extract<Part, { type: "file" }>>;
    /** A file a line shares, read through the line: its channel's
     *  visibility and withholding decide, not the file's own path. */
    file(channelId: string, lineId: string, fileId: string): Promise<Blob>;
    inbox(): Promise<Inbox>;
    search(q: string, options: { cursor?: string; channelId?: string }): Promise<Page<SearchHit>>;
    /** Every group one bot is in, with its settings. */
    sources(surfaceName: string): Promise<BotGroup[]>;
    source(channelId: string, sourceId: string): Promise<SourceSettings>;
    updateSource(channelId: string, sourceId: string, change: SourceChange): Promise<SourceSettings>;
    startWhatsAppGroup(body: { surfaceName: string; title: string; answersOutsiders: boolean }): Promise<SourceSettings>;
    telegramLink(surfaceName: string): Promise<{ url: string; expiresAt: string }>;
    /** A ten-minute code proving, once the caller confirms, that a Telegram
     *  account is theirs. With `contactId`, only that person's account may
     *  open it. */
    identityLink(surfaceId: string, contactId?: string): Promise<{ code: string; url: string; expiresAt: string }>;
    identityLinkStatus(code: string): Promise<IdentityLinkStatus>;
    confirmIdentityLink(code: string): Promise<TelegramAccount>;
    declineIdentityLink(code: string): Promise<void>;
}

export interface TelegramAccount {
    name: string | null;
    username: string | null;
}

export type IdentityLinkStatus =
    | { state: "WAITING" | "OTHER_ACCOUNT" | "GONE" }
    | { state: "OPENED"; account: TelegramAccount };

export interface PostBody {
    text: string;
    membersOnly?: boolean;
    sourceId?: string | null;
    threadRootId?: string | null;
    mentions?: Mention[];
    files?: Extract<Part, { type: "file" }>[];
    /** The composer's name for this line: a retried send is the same line. */
    clientMessageId?: string | null;
}

type Channels = LemmaClient["channels"];

function must<T>(value: T | null, what: string): T {
    if (value === null) throw new Error("The " + what + " came back without an id.");
    return value;
}

/** The post as the API takes it: mentions and files as typed parts. */
export function postRequest(body: PostBody): Parameters<Channels["post"]>[2] {
    return {
        text: body.text,
        members_only: body.membersOnly ?? false,
        source_id: body.sourceId ?? null,
        thread_root_id: body.threadRootId ?? null,
        parts: [
            ...(body.mentions ?? []).map((mention) => ({ type: "mention", id: mention.id, kind: mention.kind as MentionPart.kind })),
            ...(body.files ?? []).map((file) => ({
                type: "file", path: file.path, name: file.name,
                ...(file.mimeType ? { mime_type: file.mimeType } : {}),
                ...(file.size !== null ? { size: file.size } : {}),
            })),
        ],
        client_message_id: body.clientMessageId ?? null,
    };
}

/** A channel change as the API takes it: only the fields present. */
export function updateRequest(change: ChannelChange): Parameters<Channels["update"]>[2] {
    const body: Record<string, unknown> = {};
    if (change.title !== undefined) body.title = change.title;
    if (change.topic !== undefined) body.topic = change.topic;
    if (change.archived !== undefined) body.archived = change.archived;
    if (change.visibility !== undefined) body.visibility = change.visibility;
    if (change.answersShared !== undefined) body.answers_shared = change.answersShared;
    if (change.agentsAnswer !== undefined) body.agents_answer = change.agentsAnswer;
    if (change.handBackAfterHours !== undefined) body.hand_back_after_hours = change.handBackAfterHours;
    if (change.assignee !== undefined) body.assignee = change.assignee;
    if (change.status !== undefined) body.status = change.status;
    return body as Parameters<Channels["update"]>[2];
}

function liveApi(podId: string): ChannelApi {
    const client = () => lemma(podId);
    const detail = (raw: unknown) => must(readChannelDetail(raw), "channel");
    const settings = (raw: unknown) => must(readSourceSettings(raw), "group");
    return {
        async list({ before, ...options }) {
            return readChannelPage(await client().channels.list(podId, { ...options, cursor: before }));
        },
        async get(channelId) {
            return detail(await client().channels.get(podId, channelId));
        },
        async lines(channelId, beforeSeq) {
            return readLinePage(await client().channels.messages(podId, channelId, { limit: 50, beforeSeq }));
        },
        async thread(channelId, rootId, afterSeq) {
            return readThreadPage(await client().channels.thread(podId, channelId, rootId, { limit: 100, afterSeq }));
        },
        async post(channelId, body) {
            return must(readLine(await client().channels.post(podId, channelId, postRequest(body))), "line");
        },
        async edit(channelId, lineId, text, parts) {
            const body = parts ? { text, parts: writeParts(parts) as NonNullable<Parameters<Channels["post"]>[2]["parts"]> } : text;
            return must(readLine(await client().channels.edit(podId, channelId, lineId, body)), "line");
        },
        async remove(channelId, lineId) {
            await client().channels.deleteMessage(podId, channelId, lineId);
        },
        async share(channelId, lineId) {
            return must(readLine(await client().channels.share(podId, channelId, lineId)), "line");
        },
        async markRead(channelId, seq, threadRootId) {
            await client().channels.markRead(podId, channelId, seq, threadRootId);
        },
        async create(body) {
            return detail(await client().channels.create(podId, body));
        },
        async update(channelId, change) {
            return detail(await client().channels.update(podId, channelId, updateRequest(change)));
        },
        async destroy(channelId) {
            await client().channels.delete(podId, channelId);
        },
        async addMembers(channelId, body) {
            return detail(await client().channels.addMembers(podId, channelId, body));
        },
        async updateMember(channelId, member, change) {
            await client().channels.updateMember(podId, channelId, member.kind, member.id, change as Parameters<Channels["updateMember"]>[4]);
        },
        async removeMember(channelId, member) {
            await client().channels.removeMember(podId, channelId, member.kind, member.id);
        },
        async join(channelId) {
            return detail(await client().channels.join(podId, channelId));
        },
        async leave(channelId) {
            await client().channels.leave(podId, channelId);
        },
        async setMine(channelId, change) {
            const body: Record<string, unknown> = {};
            if (change.notify !== undefined) body.notify = WireNotifyLevel[change.notify];
            if (change.notifyVia !== undefined) body.notify_via = change.notifyVia;
            if (change.mutedUntil !== undefined) body.muted_until = change.mutedUntil;
            return detail(await client().channels.updateMyMembership(podId, channelId, body as Parameters<Channels["updateMyMembership"]>[2]));
        },
        async setAgentAnswers(channelId, on) {
            return detail(await client().channels.setAgentAnswers(podId, channelId, on));
        },
        async retryDelivery(channelId, lineId, deliveryId) {
            await client().channels.retryDelivery(podId, channelId, lineId, deliveryId);
        },
        async attach(channelId, file, line) {
            const written = await client().files.upload(file, { name: line.name, directoryPath: attachmentFolder(channelId, line.clientMessageId), searchEnabled: true });
            return { type: "file", path: written.path, name: written.name ?? line.name, mimeType: file.type || null, size: file.size, fileId: null };
        },
        async file(channelId, lineId, fileId) {
            return client().channels.file(podId, channelId, lineId, fileId);
        },
        async inbox() {
            return readInbox(await client().channels.inbox(podId));
        },
        async search(q, options) {
            return readSearchPage(await client().channels.search(podId, q, { ...options, limit: 30 }));
        },
        async sources(surfaceName) {
            const listed = await client().channels.sources.list(podId, surfaceName, { limit: 100 });
            return listed.items.flatMap((row) => {
                const read = readSourceSettings(row.source_settings);
                return read ? [{ ...read, joined: (row as { joined?: boolean }).joined !== false }] : [];
            });
        },
        async source(channelId, sourceId) {
            return settings(await client().channels.sources.get(podId, channelId, sourceId));
        },
        async updateSource(channelId, sourceId, change) {
            return settings(await client().channels.sources.update(podId, channelId, sourceId, change));
        },
        async startWhatsAppGroup(body) {
            return settings(await client().channels.sources.startWhatsAppGroup(podId, {
                surface_name: body.surfaceName,
                title: body.title,
                answers_outsiders: body.answersOutsiders,
            }));
        },
        async telegramLink(surfaceName) {
            const link = await client().channels.sources.telegramLink(podId, surfaceName);
            return { url: link.url, expiresAt: link.expires_at };
        },
        async identityLink(surfaceId, contactId) {
            const link = await client().channels.sources.identityLink(podId, surfaceId, { contactId });
            return { code: link.code, url: link.url, expiresAt: link.expires_at };
        },
        async identityLinkStatus(code) {
            const found = await client().channels.sources.identityLinkStatus(podId, code);
            if (found.state === "OPENED" && found.account) {
                return { state: "OPENED", account: { name: found.account.name ?? null, username: found.account.username ?? null } };
            }
            return { state: found.state === "OPENED" ? "GONE" : found.state };
        },
        async confirmIdentityLink(code) {
            const account = await client().channels.sources.confirmIdentityLink(podId, code);
            return { name: account.name ?? null, username: account.username ?? null };
        },
        async declineIdentityLink(code) {
            await client().channels.sources.declineIdentityLink(podId, code);
        },
    };
}

/** The sample's answers, loaded only when asked: the fixtures are large, and a
 *  live session never needs them. */
function sampleApi(podId: string): ChannelApi {
    return new Proxy({} as ChannelApi, {
        get: (_target, name) => async (...args: unknown[]) => {
            const { sampleChannelApi } = await import("./sample-api");
            const api = sampleChannelApi(podId) as unknown as Record<string, (...given: unknown[]) => Promise<unknown>>;
            return api[String(name)](...args);
        },
    });
}

export function channelApi(podId: string): ChannelApi {
    return source.label === "sample" ? sampleApi(podId) : liveApi(podId);
}
