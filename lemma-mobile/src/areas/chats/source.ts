import type { PickedFile } from "../../data/io";
import type { ConversationRef } from "../../data/types";
import type { Contact, ContactReach, NewWebWidget, OutreachSent, WebWidget, WidgetAnswer, WidgetDraft } from "@web/data/contacts";
import type { Mention } from "@web/channels/mentions";
import type {
    AgentAnswers,
    Channel,
    ChannelDetail,
    ContactStatus,
    Line,
    LinePage,
    MemberRole,
    NotifyLevel,
    Page,
    Part,
    ThreadPage,
    Visibility,
} from "@web/channels/model";
import type { SearchHit } from "@web/channels/search-model";
import type { SourceChange, SourceSettings } from "@web/channels/sources";
import type { Surface } from "@web/data/types";
import type { ListFilter } from "@web/channels/views";
import type { Inbox, PersonRef } from "@web/channels/waiting";

/** Chats across Lemmas, groups, people and contacts (PARITY.md, E).
 *
 *  Everything these screens read or change, behind one interface with a
 *  live side (`live.ts`) and a sample side (`sample.ts`). Screens reach it
 *  only through `useArea("chats")`, never the SDK directly, so sample data
 *  and a live account are the same screens.
 *
 *  The channel half is the web's `ChannelApi` (`src/channels/api.ts`) with the
 *  Lemma named on each call, since one source serves every Lemma; the rest is
 *  what the web's Chats and Contacts places read beside it. */

export type FilePart = Extract<Part, { type: "file" }>;

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
    /** Platforms to hear of it on; null is the Lemma's rule. */
    notifyVia?: string[] | null;
    /** Muted until then; null unmutes. */
    mutedUntil?: string | null;
}

export interface PostBody {
    text: string;
    membersOnly?: boolean;
    sourceId?: string | null;
    threadRootId?: string | null;
    mentions?: Mention[];
    files?: FilePart[];
    /** The composer's name for this line: a retried send is the same line. */
    clientMessageId?: string | null;
}

/** Somebody in the Lemma, as its people list says them. */
export interface LemmaPerson {
    userId: string;
    name: string;
    email: string | null;
    /** "Admin", "Editor": the role as the roster says it. */
    role: string;
}

/** One of the Lemma's agents. `front` is the Lemma itself: it answers under
 *  the Lemma's own name, in every chat. */
export interface AgentRef {
    id: string;
    name: string;
    label: string;
    front: boolean;
    blurb: string;
}

/** A group one bot is in, with whether the reader is in it. */
export type BotGroup = SourceSettings & { joined: boolean };

/** Whom one of the Lemma's own numbers, bots or addresses answers when a
 *  stranger writes to it, and whether it is the Lemma's own at all. */
export interface BotAnswer {
    answer: WidgetAnswer;
    /** Lemma's shared number and bot are every Lemma's at once: they never
     *  answer contacts. */
    own: boolean;
}

/** A run carrying a wait, as much of it as a form needs (`waiting.ts`). */
export type WaitingRun = { active_wait?: { node_id?: string; payload?: { input_schema?: unknown; ui_schema?: unknown } | null } | null } | null;

export interface ChatsSource {
    /* ── channels ─────────────────────────────────────────────────── */
    list(podId: string, filter: ListFilter & { contactId?: string; before?: string; limit?: number; q?: string }): Promise<Page<Channel>>;
    get(podId: string, channelId: string): Promise<ChannelDetail>;
    /** The newest page of top-level lines, or the one before line `beforeSeq`. */
    lines(podId: string, channelId: string, beforeSeq?: number): Promise<LinePage>;
    /** A page of a thread's replies, oldest first. */
    thread(podId: string, channelId: string, rootId: string, afterSeq?: number): Promise<ThreadPage>;
    post(podId: string, channelId: string, body: PostBody): Promise<Line>;
    edit(podId: string, channelId: string, lineId: string, text: string, parts?: readonly Part[]): Promise<Line>;
    remove(podId: string, channelId: string, lineId: string): Promise<void>;
    share(podId: string, channelId: string, lineId: string): Promise<Line>;
    markRead(podId: string, channelId: string, seq: number, threadRootId?: string | null): Promise<void>;
    create(podId: string, body: { title: string; topic: string | null; members: string[]; visibility: Visibility; agents?: { id: string; answers: AgentAnswers }[] }): Promise<ChannelDetail>;
    update(podId: string, channelId: string, change: ChannelChange): Promise<ChannelDetail>;
    destroy(podId: string, channelId: string): Promise<void>;
    addMembers(podId: string, channelId: string, body: { people?: string[]; agents?: { id: string; answers: AgentAnswers }[] }): Promise<ChannelDetail>;
    updateMember(podId: string, channelId: string, member: Pick<PersonRef, "kind"> & { id: string }, change: { role?: MemberRole; answers?: AgentAnswers }): Promise<void>;
    removeMember(podId: string, channelId: string, member: Pick<PersonRef, "kind"> & { id: string }): Promise<void>;
    join(podId: string, channelId: string): Promise<ChannelDetail>;
    leave(podId: string, channelId: string): Promise<void>;
    setMine(podId: string, channelId: string, change: MineChange): Promise<ChannelDetail>;
    setAgentAnswers(podId: string, channelId: string, on: boolean): Promise<ChannelDetail>;
    retryDelivery(podId: string, channelId: string, lineId: string, deliveryId: string): Promise<void>;
    /** Sample data only: who is typing in a chat (or one thread of it) when
     *  it opens, since sample data has no socket to say so. */
    typists?(podId: string, channelId: string): { who: PersonRef; threadRootId: string | null }[];
    /** A file from the phone for a line to share: uploaded to the writer's own
     *  files, in the line's own folder, and named as a part. */
    attach(podId: string, channelId: string, file: PickedFile, line: { clientMessageId: string; name: string }): Promise<FilePart>;
    /** A file a line shares, read through the line onto the phone: its local
     *  address, to show or hand to another app. */
    lineFile(podId: string, channelId: string, lineId: string, file: { fileId: string; name: string }): Promise<string>;
    /** Answer the question a line asks. */
    answer(podId: string, channelId: string, lineId: string, given: Record<string, unknown>): Promise<void>;
    inbox(podId: string): Promise<Inbox>;
    search(podId: string, q: string, options: { cursor?: string; channelId?: string }): Promise<Page<SearchHit>>;
    /** Who has the app open now, by user id. Empty when it cannot be asked. */
    online(podId: string): Promise<string[]>;

    /* ── groups fed from outside ──────────────────────────────────── */
    source(podId: string, channelId: string, sourceId: string): Promise<SourceSettings>;
    updateSource(podId: string, channelId: string, sourceId: string, change: SourceChange): Promise<SourceSettings>;
    startWhatsAppGroup(podId: string, body: { surfaceName: string; title: string; answersOutsiders: boolean }): Promise<SourceSettings>;
    telegramLink(podId: string, surfaceName: string): Promise<{ url: string; expiresAt: string }>;
    surfaces(podId: string): Promise<Surface[]>;

    /* ── the Lemma around them ────────────────────────────────────── */
    agents(podId: string): Promise<AgentRef[]>;
    people(podId: string): Promise<LemmaPerson[]>;
    /** The actions the reader holds in the Lemma (`podPermissions.me`), or
     *  null when everything is allowed (sample data). */
    abilities(podId: string): Promise<string[] | null>;
    /** Chats with the Lemma that live on no channel, a page at a time, most
     *  recently active first: every one of its agents', searched on the
     *  server by title. */
    conversations(podId: string, cursor: string | null, search: string | null): Promise<{ items: ConversationRef[]; next: string | null }>;
    /** A workflow run, for the form a question waits on. */
    workflowRun(podId: string, runId: string): Promise<WaitingRun>;
    submitForm(podId: string, runId: string, nodeId: string, inputs: Record<string, unknown>): Promise<void>;

    /* ── contacts ─────────────────────────────────────────────────── */
    contacts(podId: string): Promise<Contact[]>;
    renameContact(podId: string, contactId: string, name: string | null): Promise<Contact>;
    forgetContact(podId: string, contactId: string): Promise<void>;
    /** Everything the Lemma holds about a contact, as JSON text. */
    exportContact(podId: string, contactId: string): Promise<string>;
    followUp(podId: string, contactId: string, message: string, handle: { kind: string; value: string }, clientMessageId: string): Promise<OutreachSent>;
    widgets(podId: string): Promise<WebWidget[]>;
    createWidget(podId: string, draft: WidgetDraft): Promise<NewWebWidget>;
    updateWidget(podId: string, widgetId: string, change: { answer?: WidgetAnswer }): Promise<WebWidget>;
    reissueWidget(podId: string, widgetId: string): Promise<string>;
    deleteWidget(podId: string, widgetId: string): Promise<void>;
    reach(podId: string): Promise<ContactReach>;
    setTableContactOwned(podId: string, table: string, on: boolean, columns?: string[]): Promise<void>;
    setFunctionContactsInvoke(podId: string, fn: string, on: boolean): Promise<void>;
    /** A table's own columns, the ones a member could show contacts. */
    shareableColumns(podId: string, table: string): Promise<string[]>;
    /** The line said once to a contact who writes while nobody answers. */
    autoAcknowledge(podId: string): Promise<string | null>;
    setAutoAcknowledge(podId: string, said: string | null): Promise<void>;
    botAnswer(podId: string, surfaceName: string): Promise<BotAnswer>;
    setBotAnswer(podId: string, surfaceName: string, answer: WidgetAnswer): Promise<void>;
}
