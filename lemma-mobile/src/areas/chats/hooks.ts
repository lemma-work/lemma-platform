import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppState } from "react-native";
import { useInfiniteQuery, useQuery, useQueryClient, type InfiniteData, type QueryClient } from "@tanstack/react-query";
import { useCacheKey, useMe } from "../../data/hooks";
import { liveAvailable, podLive, pollWhenDown, useLapsing, useLiveStatus, useLiveTopic } from "../../live/pod-live";
import type { LiveStatus } from "@web/live/socket";
import { useArea } from "../use-area";
import { EVERYTHING, readAbilities, type Abilities } from "@web/channels/abilities";
import { HeldUpdates } from "@web/channels/held-updates";
import {
    answeringStep,
    mergeChannel,
    readChannelEvent,
    readInboxEvent,
    upsertThread,
    upsertTimeline,
    withChannel,
    withChannelUnread,
    withInboxTotals,
    withLastLinePages,
    withReadSeq,
    type Answering,
    type ChannelEvent,
} from "@web/channels/live";
import { joinPages, joinThread, senderLabel, type Channel, type ChannelDetail, type Line, type LinePage, type Page, type ThreadPage } from "@web/channels/model";
import { SEE_CONTACTS } from "@web/data/pod-roles";
import type { ListFilter } from "@web/channels/views";
import type { Inbox, PersonRef } from "@web/channels/waiting";

/** The chats screens' reads and writes, through react-query: the web's
 *  `channels/queries.ts`, `use-channel-live.ts`, `use-presence.ts` and the
 *  bell's `useInbox`, keyed by `useCacheKey()` first, so sample data and
 *  each server keep their own cache entries. */
export function useChatsSource() {
    return useArea("chats");
}

/** The reader's own id, or null while it is being read. */
export function useMyId(): string | null {
    return useMe().data?.id ?? null;
}

/** The keys, written once. Everything about one Lemma's chats starts
 *  `[key, "chats", podId]`, so an answer given anywhere is read again
 *  everywhere it shows. */
export const chatKeys = {
    pod: (key: string, podId: string) => [key, "chats", podId] as const,
    lists: (key: string, podId: string) => [key, "chats", podId, "list"] as const,
    list: (key: string, podId: string, filter: object) => [key, "chats", podId, "list", filter] as const,
    detail: (key: string, podId: string, channelId: string) => [key, "chats", podId, "detail", channelId] as const,
    lines: (key: string, podId: string, channelId: string) => [key, "chats", podId, "lines", channelId] as const,
    thread: (key: string, podId: string, channelId: string, rootId: string) => [key, "chats", podId, "thread", channelId, rootId] as const,
    threads: (key: string, podId: string, channelId: string) => [key, "chats", podId, "thread", channelId] as const,
    source: (key: string, podId: string, sourceId: string) => [key, "chats", podId, "source", sourceId] as const,
    inbox: (key: string, podId: string) => [key, "chats", podId, "inbox"] as const,
    file: (key: string, podId: string, fileId: string) => [key, "chats", podId, "file", fileId] as const,
    search: (key: string, podId: string, q: string) => [key, "chats", podId, "search", q] as const,
    conversations: (key: string, podId: string, search: string) => [key, "chats", podId, "conversations", search] as const,
    agents: (key: string, podId: string) => [key, "chats", podId, "agents"] as const,
    people: (key: string, podId: string) => [key, "chats", podId, "people"] as const,
    surfaces: (key: string, podId: string) => [key, "chats", podId, "surfaces"] as const,
    abilities: (key: string, podId: string) => [key, "chats", podId, "abilities"] as const,
    online: (key: string, podId: string) => [key, "chats", podId, "online"] as const,
    run: (key: string, podId: string, runId: string) => [key, "chats", podId, "run", runId] as const,
    contacts: (key: string, podId: string) => [key, "chats", podId, "contacts"] as const,
    widgets: (key: string, podId: string) => [key, "chats", podId, "widgets"] as const,
    reach: (key: string, podId: string) => [key, "chats", podId, "reach"] as const,
    columns: (key: string, podId: string, table: string) => [key, "chats", podId, "columns", table] as const,
    ack: (key: string, podId: string) => [key, "chats", podId, "ack"] as const,
    bot: (key: string, podId: string, name: string) => [key, "chats", podId, "bot", name] as const,
};

/** How often an open chat is read again over REST: only while the live
 *  socket is down. While it is up, the socket says what changed. */
export const READ_EVERY_MS = 10_000;

/** After anything about a chat changed: the lists, the inbox and the core
 *  rows on Home and the Lemmas tab say what is new, so all are read again. */
export function refreshLists(cache: QueryClient, key: string, podId: string): void {
    void cache.invalidateQueries({ queryKey: chatKeys.lists(key, podId) });
    void cache.invalidateQueries({ queryKey: chatKeys.inbox(key, podId) });
    void cache.invalidateQueries({ queryKey: [key, "pulse"] });
}

/** The chat as saved, written where it is held, and the lists read again. */
export function keepChannel(cache: QueryClient, key: string, podId: string, saved: ChannelDetail): void {
    cache.setQueryData(chatKeys.detail(key, podId, saved.id), saved);
    refreshLists(cache, key, podId);
}

/** Where one line is held: the timeline, or -- a reply -- its thread. */
export function lineKeys(key: string, podId: string, channelId: string, line: { threadRootId: string | null }): (readonly unknown[])[] {
    return line.threadRootId ? [chatKeys.thread(key, podId, channelId, line.threadRootId), chatKeys.lines(key, podId, channelId)] : [chatKeys.lines(key, podId, channelId)];
}

/** The Lemma's chats, most recently active first, a page at a time. `poll`
 *  overrides the fallback, for a sheet waiting on a group to arrive. */
export function useChannelList(podId: string, filter: ListFilter & { q?: string }, poll: number | false | null = null, enabled = true) {
    const source = useChatsSource();
    const key = useCacheKey();
    const status = useLiveStatus(podId);
    return useInfiniteQuery({
        queryKey: chatKeys.list(key, podId, filter),
        queryFn: ({ pageParam }) => source.list(podId, { ...filter, before: pageParam }),
        initialPageParam: undefined as string | undefined,
        getNextPageParam: (last: Page<Channel>) => last.nextBefore ?? undefined,
        staleTime: 15_000,
        enabled,
        refetchInterval: poll ?? pollWhenDown(status, READ_EVERY_MS * 3),
    });
}

/** Every page read so far, as one list. */
export function allChannels(data: InfiniteData<Page<Channel>> | undefined): Channel[] {
    const seen = new Set<string>();
    return (data?.pages ?? []).flatMap((page) => page.items).filter((channel) => !seen.has(channel.id) && Boolean(seen.add(channel.id)));
}

export function useChannel(podId: string, channelId: string) {
    const source = useChatsSource();
    const key = useCacheKey();
    const status = useLiveStatus(podId);
    return useQuery({
        queryKey: chatKeys.detail(key, podId, channelId),
        queryFn: () => source.get(podId, channelId),
        refetchInterval: pollWhenDown(status, READ_EVERY_MS * 3),
        staleTime: 10_000,
        retry: (count, error) => count < 2 && !/not here|not in it|may not/i.test(error instanceof Error ? error.message : ""),
    });
}

/** A chat's timeline: the newest page first, older pages as asked for. */
export function useLines(podId: string, channelId: string) {
    const source = useChatsSource();
    const key = useCacheKey();
    const status = useLiveStatus(podId);
    const query = useInfiniteQuery({
        queryKey: chatKeys.lines(key, podId, channelId),
        queryFn: ({ pageParam }) => source.lines(podId, channelId, pageParam),
        initialPageParam: undefined as number | undefined,
        getNextPageParam: (last: LinePage) => last.nextBefore ?? undefined,
        refetchInterval: pollWhenDown(status, READ_EVERY_MS),
        staleTime: status === "open" ? 60_000 : 5_000,
    });
    const lines = useMemo(() => joinPages(query.data?.pages ?? []), [query.data]);
    return { ...query, lines };
}

/** A thread's replies, a page at a time from the first. */
export function useThread(podId: string, channelId: string, rootId: string, enabled = true) {
    const source = useChatsSource();
    const key = useCacheKey();
    const status = useLiveStatus(podId);
    const query = useInfiniteQuery({
        queryKey: chatKeys.thread(key, podId, channelId, rootId),
        queryFn: ({ pageParam }) => source.thread(podId, channelId, rootId, pageParam),
        initialPageParam: undefined as number | undefined,
        getNextPageParam: (last: ThreadPage) => last.nextAfter ?? undefined,
        enabled,
        refetchInterval: enabled ? pollWhenDown(status, READ_EVERY_MS) : false,
        staleTime: 5_000,
    });
    const replies = useMemo(() => joinThread(query.data?.pages ?? []), [query.data]);
    return { ...query, replies };
}

/** What is new for the reader in this Lemma: the counts, the questions
 *  waiting on them and the lines naming them. Read once and kept by the
 *  socket's `inbox` topic (`useInboxLive`); read on a poll only while the
 *  socket is down. */
export function useInbox(podId: string, enabled = true) {
    const source = useChatsSource();
    const key = useCacheKey();
    const status = useLiveStatus(podId);
    return useQuery({
        queryKey: chatKeys.inbox(key, podId),
        enabled,
        queryFn: (): Promise<Inbox> => source.inbox(podId),
        staleTime: 30_000,
        refetchInterval: pollWhenDown(status, 60_000),
    });
}

/** The `inbox` topic, applied (the web's `useInboxLive`): counts are
 *  absolute, so each event is the truth for its chat and the totals; a
 *  question new to the reader is read with the inbox again, so it arrives
 *  answerable. */
export function useInboxLive(podId: string, enabled = true): void {
    const cache = useQueryClient();
    const key = useCacheKey();
    const later = useRef<ReturnType<typeof setTimeout> | null>(null);
    useEffect(() => () => { if (later.current) clearTimeout(later.current); }, []);
    const reread = () => void cache.invalidateQueries({ queryKey: chatKeys.inbox(key, podId) });
    useLiveTopic(podId, "inbox", enabled ? {} : null, {
        onSubscribed: reread,
        onResync: reread,
        onEvents: (events) => {
            for (const raw of events) {
                const event = readInboxEvent(raw);
                if (!event) continue;
                if (event.type === "question.waiting") { reread(); continue; }
                cache.setQueryData<Inbox>(chatKeys.inbox(key, podId), (was) => (was ? withInboxTotals(was, event.totals) : was));
                cache.setQueriesData<InfiniteData<Page<Channel>>>({ queryKey: chatKeys.lists(key, podId) }, (was) => was
                    ? { ...was, pages: was.pages.map((page) => ({ ...page, items: withChannelUnread(page.items, event.channelId, event.unread) })) }
                    : was);
                if (later.current) clearTimeout(later.current);
                later.current = setTimeout(() => {
                    reread();
                    void cache.invalidateQueries({ queryKey: chatKeys.lists(key, podId) });
                    void cache.invalidateQueries({ queryKey: [key, "pulse"] });
                }, 1_500);
            }
        },
    });
}

/** The Lemma's agents, the Lemma itself first. */
export function useAgents(podId: string) {
    const source = useChatsSource();
    const key = useCacheKey();
    return useQuery({ queryKey: chatKeys.agents(key, podId), queryFn: () => source.agents(podId), staleTime: 5 * 60_000 });
}

/** The people in the Lemma. */
export function usePeople(podId: string) {
    const source = useChatsSource();
    const key = useCacheKey();
    return useQuery({ queryKey: chatKeys.people(key, podId), queryFn: () => source.people(podId), staleTime: 2 * 60_000 });
}

/** The Lemma's own numbers, bots and addresses. */
export function useSurfaces(podId: string) {
    const source = useChatsSource();
    const key = useCacheKey();
    return useQuery({ queryKey: chatKeys.surfaces(key, podId), queryFn: () => source.surfaces(podId), staleTime: 60_000 });
}

/** What the reader may do in the Lemma's chats (`podPermissions.me`). The
 *  server decides each again; this only stops the phone offering what it
 *  would refuse. Sample data may do everything. */
export function useAbilities(podId: string): Abilities & { held: string[] | null } {
    const source = useChatsSource();
    const key = useCacheKey();
    const asked = useQuery({ queryKey: chatKeys.abilities(key, podId), queryFn: () => source.abilities(podId), staleTime: 60_000 });
    if (asked.data === null) return { ...EVERYTHING, held: null };
    return { ...readAbilities(asked.data), held: asked.data ?? [] };
}

/** Whether the reader may see this Lemma's contacts at all (the web's
 *  `useMaySeeContacts`): unknown counts as no, so the place is not offered
 *  while the question is out. */
export function useMaySeeContacts(podId: string): boolean {
    const abilities = useAbilities(podId);
    if (abilities.held === null) return abilities.known;
    return SEE_CONTACTS.some((action) => abilities.held?.includes(action));
}

/** Who in the Lemma has the app open now, by user id: the socket's
 *  `presence` call, asked while a list of people is on screen. Empty when
 *  the socket is down: nobody is shown as away on a guess. */
export function useOnline(podId: string, enabled = true): ReadonlySet<string> {
    const source = useChatsSource();
    const key = useCacheKey();
    const status = useLiveStatus(podId);
    const sample = !liveAvailable();
    const asked = useQuery({
        queryKey: chatKeys.online(key, podId),
        enabled: enabled && (sample || status === "open"),
        refetchInterval: 60_000,
        staleTime: 30_000,
        retry: false,
        queryFn: () => source.online(podId),
    });
    return useMemo(() => new Set(sample || status === "open" ? asked.data ?? [] : []), [asked.data, sample, status]);
}

/** Every chat with the Lemma that lives on no channel, a page at a time,
 *  searched on the server by title. */
export function useConversations(podId: string, search: string, enabled = true) {
    const source = useChatsSource();
    const key = useCacheKey();
    return useInfiniteQuery({
        queryKey: chatKeys.conversations(key, podId, search),
        queryFn: ({ pageParam }) => source.conversations(podId, pageParam, search || null),
        initialPageParam: null as string | null,
        getNextPageParam: (last) => last.next,
        enabled,
        staleTime: 30_000,
    });
}

/** What was said across the chats the reader may see, a page at a time. */
export function useSaid(podId: string, q: string, enabled = true) {
    const source = useChatsSource();
    const key = useCacheKey();
    return useInfiniteQuery({
        queryKey: chatKeys.search(key, podId, q),
        enabled: enabled && q.length >= 2,
        queryFn: ({ pageParam }) => source.search(podId, q, { cursor: pageParam }),
        initialPageParam: undefined as string | undefined,
        getNextPageParam: (last) => last.nextBefore ?? undefined,
        staleTime: 30_000,
    });
}

/** Typing settles before a search is asked for. */
export function useSettled(value: string, ms = 300): string {
    const [settled, setSettled] = useState(value);
    useEffect(() => {
        const timer = setTimeout(() => setSettled(value.trim()), ms);
        return () => clearTimeout(timer);
    }, [value, ms]);
    return settled;
}

/** Whether the app is in front: what is on screen behind another app has
 *  not been read. */
export function useAppShown(): boolean {
    const [shown, setShown] = useState(AppState.currentState === "active");
    useEffect(() => {
        const listening = AppState.addEventListener("change", (state) => setShown(state === "active"));
        return () => listening.remove();
    }, []);
    return shown;
}

/* ── one chat, live ─────────────────────────────────────────────────── */

/** Where each chat's subscription had got to, so a chat left and opened
 *  again resumes from its cursor rather than reading the timeline again. */
const cursors = new Map<string, string | null>();

/** How long "Priya is typing" stays up after the last note of it. */
const TYPING_SHOWN_MS = 5_000;
/** The least time between two typing notes from this phone. */
export const TYPING_EVERY_MS = 3_000;

export interface Typist {
    who: PersonRef;
    threadRootId: string | null;
}

export interface ChannelLive {
    status: LiveStatus;
    typing: Typist[];
    answering: Answering[];
    /** The last new line from somebody else, for a screen reader. */
    announced: string;
    /** "I am typing here" -- in the chat, or one thread of it. */
    typed(threadRootId?: string | null): void;
    /** Read the timeline to `seq` -- or, with `threadRootId`, that thread. */
    markRead(seq: number, threadRootId?: string | null): Promise<number>;
}

/** One chat kept live while it is on screen (the web's `useChannelLive`):
 *  its timeline, its threads, the chat itself and the reader's read
 *  position follow the socket; typing and an answer being written are held
 *  here. Where there is no socket -- sample data -- it reads over REST. */
export function useChannelLive(podId: string, channel: ChannelDetail, me: string | null): ChannelLive {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const status = useLiveStatus(podId);
    const [typing, heardTyping] = useLapsing<Typist>(TYPING_SHOWN_MS);
    const [answering, setAnswering] = useState<Answering[]>([]);
    const [announced, setAnnounced] = useState("");
    const lastTyped = useRef(0);
    const lastTypedIn = useRef<string | null>(null);
    const where = key + ":" + podId + ":" + channel.id;
    const [held] = useState(() => new HeldUpdates());
    useEffect(() => held.watch(cache), [held, cache]);

    const applyLine = (event: Extract<ChannelEvent, { line: Line }>) => {
        const line = event.line;
        if (line.threadRootId) {
            held.apply<InfiniteData<ThreadPage>>(cache, chatKeys.thread(key, podId, channel.id, line.threadRootId), (was) => ({ ...was, pages: upsertThread(was.pages, line) }));
        } else {
            held.apply<InfiniteData<LinePage>>(cache, chatKeys.lines(key, podId, channel.id), (was) => ({ ...was, pages: upsertTimeline(was.pages, line).pages }));
            if (event.type === "line.created") {
                cache.setQueriesData<InfiniteData<Page<Channel>>>({ queryKey: chatKeys.lists(key, podId) }, (was) => (was ? { ...was, pages: withLastLinePages(was.pages, channel.id, line) } : was));
            }
        }
        cache.setQueryData<ChannelDetail>(chatKeys.detail(key, podId, channel.id), (was) => (was && line.seq > was.lastSeq ? { ...was, lastSeq: line.seq, lastMessageAt: line.createdAt } : was));
        const mine = line.sender.kind === "MEMBER" && line.sender.id === me;
        if (event.type === "line.created" && !mine) {
            const said = line.withheld ? "answered someone" : line.deleted ? "" : (line.text ?? "").replace(/\s+/g, " ").slice(0, 160);
            setAnnounced(senderLabel(line, me) + (line.threadRootId ? ", in a thread" : "") + ": " + said);
        }
    };

    const apply = (event: ChannelEvent) => {
        switch (event.type) {
            case "line.created":
            case "line.edited":
            case "line.deleted":
                applyLine(event);
                if (event.line.interaction) void cache.invalidateQueries({ queryKey: chatKeys.inbox(key, podId) });
                return;
            case "question.closed":
                void cache.invalidateQueries({ queryKey: chatKeys.inbox(key, podId) });
                return;
            case "channel.updated":
                cache.setQueryData<ChannelDetail>(chatKeys.detail(key, podId, channel.id), (was) => (was ? mergeChannel(was, event.channel) : was));
                cache.setQueriesData<InfiniteData<Page<Channel>>>({ queryKey: chatKeys.lists(key, podId) }, (was) => was
                    ? { ...was, pages: was.pages.map((page) => ({ ...page, items: withChannel(page.items, event.channel) })) }
                    : was);
                void cache.invalidateQueries({ queryKey: chatKeys.detail(key, podId, channel.id) });
                return;
            case "read.updated":
                cache.setQueryData<ChannelDetail>(chatKeys.detail(key, podId, channel.id), (was) => (was ? withReadSeq(was, event.readSeq) : was));
                return;
            case "typing":
                if (!(event.user.kind === "MEMBER" && event.user.id === me)) {
                    heardTyping({ who: event.user, threadRootId: event.threadRootId }, event.user.kind + ":" + (event.user.id ?? event.user.name ?? "") + ":" + (event.threadRootId ?? ""));
                }
                return;
            case "answering.started":
            case "answering.finished":
            case "line.streaming":
                setAnswering((was) => answeringStep(was, event));
                return;
        }
    };

    const reread = () => {
        void cache.invalidateQueries({ queryKey: chatKeys.lines(key, podId, channel.id) });
        void cache.invalidateQueries({ queryKey: chatKeys.detail(key, podId, channel.id) });
        void cache.invalidateQueries({ queryKey: chatKeys.threads(key, podId, channel.id) });
    };

    const fresh = useRef(false);
    useLiveTopic(podId, "channel", { channel_id: channel.id }, {
        onSubscribed: () => {
            const state = cache.getQueryState(chatKeys.lines(key, podId, channel.id));
            if (fresh.current && (state?.data !== undefined || state?.fetchStatus === "fetching")) reread();
            fresh.current = false;
        },
        onEvents: (events) => {
            for (const raw of events) {
                const event = readChannelEvent(raw);
                if (event) apply(event);
            }
        },
        onResync: reread,
        onEnded: () => {
            cursors.delete(where);
            void cache.invalidateQueries({ queryKey: chatKeys.detail(key, podId, channel.id) });
            refreshLists(cache, key, podId);
        },
    }, { since: () => { const at = cursors.get(where) ?? null; fresh.current = at === null; return at; }, onCursor: (cursor) => cursors.set(where, cursor) });

    /* Sample data has no socket: whoever it says is typing types for a
       while after the chat opens. */
    useEffect(() => {
        if (liveAvailable() || !source.typists) return;
        const typists = source.typists(podId, channel.id);
        if (!typists.length) return;
        const hear = () => { for (const one of typists) heardTyping(one, one.who.kind + ":" + (one.who.id ?? "") + ":" + (one.threadRootId ?? "")); };
        hear();
        const every = setInterval(hear, TYPING_SHOWN_MS - 1_000);
        const stop = setTimeout(() => clearInterval(every), 24_000);
        return () => { clearInterval(every); clearTimeout(stop); };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [source, podId, channel.id]);

    const typed = useCallback((threadRootId: string | null = null) => {
        if (!liveAvailable()) return;
        const now = Date.now();
        if (now - lastTyped.current < TYPING_EVERY_MS && lastTypedIn.current === threadRootId) return;
        lastTyped.current = now;
        lastTypedIn.current = threadRootId;
        podLive(podId).call("typing", { channel_id: channel.id, ...(threadRootId ? { thread_root_id: threadRootId } : {}) })
            .catch(() => { /* a typing note is worth nothing late */ });
    }, [podId, channel.id]);

    const markRead = useCallback(async (seq: number, threadRootId?: string | null) => {
        let kept = seq;
        const thread = threadRootId ? { thread_root_id: threadRootId } : {};
        const open = liveAvailable() && podLive(podId).status === "open";
        if (open) {
            try {
                const result = (await podLive(podId).call("read", { channel_id: channel.id, seq, ...thread })) as { read_seq?: unknown } | null;
                kept = typeof result?.read_seq === "number" ? result.read_seq : seq;
            } catch {
                await source.markRead(podId, channel.id, seq, threadRootId);
            }
        } else {
            await source.markRead(podId, channel.id, seq, threadRootId);
        }
        if (!threadRootId) cache.setQueryData<ChannelDetail>(chatKeys.detail(key, podId, channel.id), (was) => (was ? withReadSeq(was, kept) : was));
        if (!open) refreshLists(cache, key, podId);
        return kept;
    }, [podId, channel.id, cache, key, source]);

    return { status, typing, answering, announced, typed, markRead };
}
